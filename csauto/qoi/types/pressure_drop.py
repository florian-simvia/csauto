"""pressure_drop — head loss between two boundary planes (inlet / outlet).

Workflow:
    1. `prepare` injects one per-boundary static helper into
       caseXXXX/SRC/cs_user_extra_operations.cpp via the cpp assembler.
       Each helper integrates over its boundary patch:
         - total_pressure * dA    (static pressure * area)
         - 0.5.rho.|V|^2 * dA        (dynamic head * area)
         - rho.g.z * dA             (gravitational potential * area)
         - int dA                   (total patch area)
       and writes one CSV row per timestep with the four area-averaged
       quantities.
    2. `postprocess` reads both CSVs (inlet + outlet), aggregates over the
       requested time window and computes dP from H_inlet - H_outlet, where
       H is defined by `mode`.

Recipe schema (TOML):
    [[qoi]]
    name = "dP"
    type = "pressure_drop"
    inlet = "INLET"                # required, boundary zone LABEL in setup.xml
    outlet = "OUTLET"              # required, boundary zone LABEL in setup.xml
    mode = "bernoulli"             # optional: "bernoulli" (default) | "static"
    aggregate = "mean_last_10pct"  # optional, default "mean_last_10pct"

Modes:
    - "bernoulli" (default) computes the **real** head loss:
        dP = (p_in + ½rhoV^2_in + rhogz_in) - (p_out + ½rhoV^2_out + rhogz_out)
      i.e. friction + minor losses, the quantity a pump must overcome.
    - "static" gives the raw static-pressure difference d(total_pressure).
      Useful when comparing to a wall pressure sensor, but **does NOT** subtract
      the elevation head rhogdz, so it mixes friction and gravity for
      non-horizontal sections.

Reads the v9 ``total_pressure`` property field (csauto activates it
automatically in setup.xml), the ``velocity`` and ``density`` fields (always
present), and ``cs_glob_physical_constants->gravity``. The gravity term uses
``-rho.(face_cog . g)``, so it works with any orientation of gravity.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from string import Template
from typing import Any

from ..cpp import CppHelper
from ..errors import QoIError, RecipeError
from ..extractor import Context
from ..recipe import Recipe
from ..registry import register

_DEFAULT_AGGREGATE = "mean_last_10pct"
_DEFAULT_MODE = "bernoulli"
_VALID_MODES = ("bernoulli", "static")
_AGGREGATE_PCT_RE = re.compile(r"^mean_last_(\d+)pct$")
_CSV_COLUMNS = ("p_avg", "ke_avg", "gh_avg", "area")
_NON_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


_HELPER_TEMPLATE = Template(
    r"""static void
${helper_name}(cs_domain_t *domain)
{
  const cs_mesh_t            *m  = domain->mesh;
  const cs_mesh_quantities_t *mq = domain->mesh_quantities;

  const cs_zone_t *z = cs_boundary_zone_by_name_try("${boundary}");
  if (z == nullptr) {
    static bool zone_warned = false;
    if (cs_glob_rank_id <= 0 && !zone_warned) {
      zone_warned = true;
      std::fprintf(stderr,
                   "[csauto] pressure_drop: boundary zone "
                   "\"${boundary}\" not found. Check setup.xml — the "
                   "name must match the zone label (not a geom "
                   "selection criterion / group). Skipping.\n");
    }
    return;
  }

  /* total_pressure is the physical static pressure with hydrostatic
     and reference offsets reconstructed (and TKE correction in RANS-EVM).
     csauto activates it in setup.xml automatically. */
  const cs_field_t *f_p   = cs_field_by_name_try("total_pressure");
  const cs_field_t *f_vel = cs_field_by_name_try("velocity");
  const cs_field_t *f_rho = cs_field_by_name_try("density");
  if (f_p == nullptr || f_vel == nullptr || f_rho == nullptr) {
    static bool warned = false;
    if (cs_glob_rank_id <= 0 && !warned) {
      warned = true;
      std::fprintf(stderr,
                   "[csauto] pressure_drop helper for boundary "
                   "\"${boundary}\" requires fields 'total_pressure', "
                   "'velocity' and 'density'. One or more is missing. "
                   "Skipping.\n");
    }
    return;
  }

  const cs_real_t *g_vec = cs_glob_physical_constants->gravity;

  cs_real_t I_p = 0.0;   /* int total_pressure dA  */
  cs_real_t I_ke = 0.0;  /* int 0.5 rho |V|^2 dA     */
  cs_real_t I_gh = 0.0;  /* int rho g z dA            */
  cs_real_t A = 0.0;     /* int dA                  */

  for (cs_lnum_t i = 0; i < z->n_elts; i++) {
    const cs_lnum_t  face_id = z->elt_ids[i];
    const cs_lnum_t  cell_id = m->b_face_cells[face_id];
    const cs_real_t  area    = mq->b_face_surf[face_id];
    const cs_real_t  p_face  = f_p->val[cell_id];
    const cs_real_t *v       = f_vel->val + 3 * cell_id;
    const cs_real_t  rho     = f_rho->val[cell_id];
    const cs_real_t *cog     = mq->b_face_cog[face_id];  /* b_face_cog is cs_real_3_t* */

    const cs_real_t V2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    /* gh = rho.g.z. With cog at height z (in the up direction) and
       g_vec pointing down, (cog . g_vec) = -|g|.z, so rho.g.z = -rho.(cog.g_vec).
       Reference height is implicit; only dz between two boundaries matters. */
    const cs_real_t cog_dot_g = cog[0] * g_vec[0] + cog[1] * g_vec[1] + cog[2] * g_vec[2];
    const cs_real_t gh        = -rho * cog_dot_g;

    I_p  += p_face * area;
    I_ke += 0.5 * rho * V2 * area;
    I_gh += gh * area;
    A    += area;
  }

  cs_parall_sum(1, CS_REAL_TYPE, &I_p);
  cs_parall_sum(1, CS_REAL_TYPE, &I_ke);
  cs_parall_sum(1, CS_REAL_TYPE, &I_gh);
  cs_parall_sum(1, CS_REAL_TYPE, &A);

  if (cs_glob_rank_id <= 0 && A > 0.0) {
    static FILE *fp = nullptr;
    if (fp == nullptr) {
      fp = std::fopen("${output_csv}", "w");
      if (fp != nullptr)
        std::fprintf(fp, "t,p_avg,ke_avg,gh_avg,area\n");
    }
    if (fp != nullptr) {
      std::fprintf(fp,
                   "%.9g,%.9g,%.9g,%.9g,%.9g\n",
                   cs_glob_time_step->t_cur,
                   I_p / A, I_ke / A, I_gh / A, A);
      std::fflush(fp);
    }
  }
}
"""
)


def output_csv_path(boundary: str) -> str:
    """Return the per-boundary CSV path written by the C++ helper."""
    return f"monitoring/csauto_pressure_{boundary}.csv"


def helper_name(boundary: str) -> str:
    sanitized = _NON_IDENT_RE.sub("_", boundary)
    if not sanitized or sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return f"csauto_qoi_pressure_{sanitized}"


def render_helper(boundary: str) -> str:
    return _HELPER_TEMPLATE.substitute(
        helper_name=helper_name(boundary),
        boundary=boundary,
        output_csv=output_csv_path(boundary),
    )


@register("pressure_drop")
class PressureDropExtractor:
    """Compute the head loss (or static pressure difference) between two boundaries."""

    def validate(self, recipe: Recipe) -> None:
        params = recipe.params
        _require_str(params, "inlet", recipe.name)
        _require_str(params, "outlet", recipe.name)
        if str(params["inlet"]).strip() == str(params["outlet"]).strip():
            raise RecipeError(
                f"pressure_drop '{recipe.name}': 'inlet' and 'outlet' must reference different boundaries"
            )
        mode = params.get("mode", _DEFAULT_MODE)
        if mode not in _VALID_MODES:
            raise RecipeError(f"pressure_drop '{recipe.name}': 'mode' must be one of {_VALID_MODES}, got {mode!r}")
        aggregate = params.get("aggregate", _DEFAULT_AGGREGATE)
        _validate_aggregate_mode(aggregate, recipe.name)

    def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]:
        inlet = str(recipe.params["inlet"])
        outlet = str(recipe.params["outlet"])
        mode = recipe.params.get("mode", _DEFAULT_MODE)
        aggregate = recipe.params.get("aggregate", _DEFAULT_AGGREGATE)

        csv_in = _resolve_pressure_csv(ctx.case_dir, inlet)
        csv_out = _resolve_pressure_csv(ctx.case_dir, outlet)
        rows_in = _read_pressure_csv(csv_in)
        rows_out = _read_pressure_csv(csv_out)
        agg_in = _aggregate_rows(rows_in, aggregate)
        agg_out = _aggregate_rows(rows_out, aggregate)

        h_in = _head(agg_in, mode)
        h_out = _head(agg_out, mode)
        return {recipe.name: h_in - h_out}

    def cpp_helpers(self, recipe: Recipe) -> list[CppHelper]:
        inlet = str(recipe.params["inlet"])
        outlet = str(recipe.params["outlet"])
        helpers: list[CppHelper] = []
        for boundary in (inlet, outlet):
            helpers.append(CppHelper(name=helper_name(boundary), code=render_helper(boundary)))
        return helpers

    def required_setup_properties(self, recipe: Recipe) -> list[str]:
        """code_saturne records total_pressure as a derived field when the
        post-processing flag is enabled. csauto toggles it on at prepare time."""
        return ["total_pressure"]


# --- Validation helpers -----------------------------------------------------


def _require_str(params: dict[str, Any], key: str, recipe_name: str) -> None:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecipeError(f"pressure_drop '{recipe_name}': '{key}' must be a non-empty string")


def _validate_aggregate_mode(mode: Any, recipe_name: str) -> None:
    if mode == "final":
        return
    if isinstance(mode, str):
        match = _AGGREGATE_PCT_RE.match(mode)
        if match:
            pct = int(match.group(1))
            if 0 < pct <= 100:
                return
    raise RecipeError(f"pressure_drop '{recipe_name}': 'aggregate' must be 'final' or 'mean_last_Npct' (0 < N <= 100)")


# --- CSV / aggregation helpers ----------------------------------------------


def _resolve_pressure_csv(case_dir: Path, boundary: str) -> Path:
    """Find the per-boundary pressure CSV. Same conventions as force_coefficient:
    prefer the latest RESU/<run_id>/monitoring/, fall back to case_dir/monitoring/."""
    rel = output_csv_path(boundary)
    resu_root = case_dir / "RESU"
    resu_subdirs: list[Path] = []
    if resu_root.is_dir():
        resu_subdirs = sorted(
            (d for d in resu_root.iterdir() if d.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for run_dir in resu_subdirs:
            candidate = run_dir / rel
            if candidate.is_file():
                return candidate
    direct = case_dir / rel
    if direct.is_file():
        return direct
    if not resu_subdirs:
        raise QoIError(
            f"pressure_drop: no RESU run found under {case_dir.name}/RESU/ "
            f"(case has not been launched yet, or all runs were cleaned)."
        )
    raise QoIError(
        f"pressure_drop: CSV not found for boundary {boundary!r} in latest "
        f"RESU {resu_subdirs[0].name} of {case_dir.name}. Check the C++ helper "
        f"ran (look for [csauto] in run_solver.log) and the boundary name "
        f"matches the zone label in setup.xml."
    )


def _read_pressure_csv(path: Path) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise QoIError(f"pressure_drop: CSV has no header: {path}")
        missing = [col for col in _CSV_COLUMNS if col not in reader.fieldnames]
        if missing:
            raise QoIError(f"pressure_drop: CSV {path} missing columns: {', '.join(missing)}")
        for raw in reader:
            try:
                rows.append({col: float(raw[col]) for col in _CSV_COLUMNS})
            except (TypeError, ValueError) as exc:
                raise QoIError(f"pressure_drop: malformed row in {path}: {raw}") from exc
    if not rows:
        raise QoIError(f"pressure_drop: CSV has no data rows: {path}")
    return rows


def _aggregate_rows(rows: list[dict[str, float]], mode: str) -> dict[str, float]:
    columns = tuple(rows[0].keys())
    if mode == "final":
        return dict(rows[-1])
    match = _AGGREGATE_PCT_RE.match(mode)
    if match is None:
        raise QoIError(f"pressure_drop: unknown aggregate mode {mode!r}")
    pct = int(match.group(1))
    window = max(1, len(rows) * pct // 100)
    tail = rows[-window:]
    return {col: sum(r[col] for r in tail) / len(tail) for col in columns}


def _head(agg: dict[str, float], mode: str) -> float:
    if mode == "static":
        return agg["p_avg"]
    if mode == "bernoulli":
        return agg["p_avg"] + agg["ke_avg"] + agg["gh_avg"]
    raise QoIError(f"pressure_drop: unknown mode {mode!r}")


__all__ = [
    "PressureDropExtractor",
    "helper_name",
    "output_csv_path",
    "render_helper",
]
