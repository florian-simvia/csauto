"""force_coefficient — Cd/Cl/Cm-style aerodynamic coefficient on a boundary.

Workflow:
    1. `prepare` injects a per-boundary static helper into
       caseXXXX/SRC/cs_user_extra_operations.cpp via the cpp assembler. The
       helper integrates pressure and viscous forces on its boundary at every
       time step and writes a CSV under monitoring/.
    2. `postprocess` reads that CSV, aggregates over the requested time
       window, projects on the user direction and normalizes by 1/2 rho U^2 A.

Multiple recipes that target the *same* boundary (e.g. Cd and Cl on "wing")
share a single helper — the C++ side writes one CSV; the Python extractor
reads it twice with different projections.

Recipe schema (TOML):
    [[qoi]]
    name = "Cd"
    type = "force_coefficient"
    boundary = "wing"                  # required, str
    direction = [1.0, 0.0, 0.0]        # required, list[float] length 3
    ref_area = 1.5                     # required, float > 0
    ref_velocity = 30.0                # required, float > 0
    ref_density = 1.225                # required, float > 0
    aggregate = "mean_last_10pct"      # optional, default "mean_last_10pct"

Supported aggregate modes:
    - "final"            -> last sample
    - "mean_last_Npct"   -> arithmetic mean of the last N% samples (e.g. mean_last_10pct)

Note: the C++ helper targets the code_saturne v9 user-file API. It is a
first draft that compiles against documented v9 symbols (cs_boundary_zone,
cs_field_by_name, cs_parall_sum, ...). It needs validation against a real
v9 install before being trusted in production — see docs/qoi.md.
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
_AGGREGATE_PCT_RE = re.compile(r"^mean_last_(\d+)pct$")
_FORCE_COLUMNS = ("Fpx", "Fpy", "Fpz", "Fvx", "Fvy", "Fvz")
_NON_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")

_HELPER_TEMPLATE = Template(
    r"""static void
${helper_name}(cs_domain_t *domain)
{
  const cs_mesh_t            *m  = domain->mesh;
  const cs_mesh_quantities_t *mq = domain->mesh_quantities;

  const cs_zone_t *z = cs_boundary_zone_by_name_try("${boundary}");
  if (z == nullptr)
    return;

  const cs_real_t   *p_val     = cs_field_by_name("pressure")->val;
  const cs_field_t  *f_bstress = cs_field_by_name_try("boundary_stress");

  cs_real_t F_pres[3] = {0.0, 0.0, 0.0};
  cs_real_t F_visc[3] = {0.0, 0.0, 0.0};

  for (cs_lnum_t i = 0; i < z->n_elts; i++) {
    const cs_lnum_t  face_id = z->elt_ids[i];
    const cs_lnum_t  cell_id = m->b_face_cells[face_id];
    const cs_real_t *n_a     = mq->b_face_normal + 3 * face_id;  /* direction * area */
    const cs_real_t  p_face  = p_val[cell_id];

    for (int j = 0; j < 3; j++)
      F_pres[j] += p_face * n_a[j];

    if (f_bstress != nullptr) {
      const cs_real_t  area = mq->b_face_surf[face_id];
      const cs_real_t *tau  = f_bstress->val + 3 * face_id;
      for (int j = 0; j < 3; j++)
        F_visc[j] += tau[j] * area;
    }
  }

  cs_parall_sum(3, CS_REAL_TYPE, F_pres);
  cs_parall_sum(3, CS_REAL_TYPE, F_visc);

  if (cs_glob_rank_id <= 0) {
    static FILE *fp = nullptr;
    if (fp == nullptr) {
      fp = std::fopen("${output_csv}", "w");
      if (fp != nullptr)
        std::fprintf(fp, "t,Fpx,Fpy,Fpz,Fvx,Fvy,Fvz\n");
    }
    if (fp != nullptr) {
      std::fprintf(fp,
                   "%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g\n",
                   cs_glob_time_step->t_cur,
                   F_pres[0], F_pres[1], F_pres[2],
                   F_visc[0], F_visc[1], F_visc[2]);
      std::fflush(fp);
    }
  }
}
"""
)


def output_csv_path(boundary: str) -> str:
    """Return the relative path (from case_dir) where the C++ writes its CSV."""
    return f"monitoring/csauto_forces_{boundary}.csv"


def helper_name(boundary: str) -> str:
    """Return the static C function name for the given boundary.

    Boundary names are sanitized to a valid C identifier suffix. Two
    boundaries that sanitize to the same suffix would collide; we keep
    the original boundary in the body via the string literal, so the
    code_saturne lookup still uses the original (non-sanitized) name.
    """
    sanitized = _NON_IDENT_RE.sub("_", boundary)
    if not sanitized or sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return f"csauto_qoi_force_{sanitized}"


def render_helper(boundary: str) -> str:
    """Render the static helper function source for one boundary."""
    return _HELPER_TEMPLATE.substitute(
        helper_name=helper_name(boundary),
        boundary=boundary,
        output_csv=output_csv_path(boundary),
    )


@register("force_coefficient")
class ForceCoefficientExtractor:
    """Compute a force-based coefficient (Cd, Cl, Cm, ...) on a boundary."""

    def validate(self, recipe: Recipe) -> None:
        params = recipe.params
        _require_str(params, "boundary", recipe.name)
        _require_direction(params.get("direction"), recipe.name)
        for key in ("ref_area", "ref_velocity", "ref_density"):
            _require_positive_number(params, key, recipe.name)
        aggregate = params.get("aggregate", _DEFAULT_AGGREGATE)
        _validate_aggregate_mode(aggregate, recipe.name)

    def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]:
        boundary = str(recipe.params["boundary"])
        csv_path = ctx.case_dir / output_csv_path(boundary)
        rows = _read_force_csv(csv_path)
        mode = recipe.params.get("aggregate", _DEFAULT_AGGREGATE)
        aggregated = _aggregate_rows(rows, mode)

        direction = tuple(float(c) for c in recipe.params["direction"])
        f_total = tuple(aggregated[f"Fp{axis}"] + aggregated[f"Fv{axis}"] for axis in ("x", "y", "z"))
        f_dir = sum(f_total[i] * direction[i] for i in range(3))

        ref_density = float(recipe.params["ref_density"])
        ref_velocity = float(recipe.params["ref_velocity"])
        ref_area = float(recipe.params["ref_area"])
        q = 0.5 * ref_density * ref_velocity * ref_velocity
        coefficient = f_dir / (q * ref_area)

        return {recipe.name: coefficient}

    def cpp_helpers(self, recipe: Recipe) -> list[CppHelper]:
        boundary = str(recipe.params["boundary"])
        return [CppHelper(name=helper_name(boundary), code=render_helper(boundary))]


# --- Validation helpers -----------------------------------------------------


def _require_str(params: dict[str, Any], key: str, recipe_name: str) -> None:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RecipeError(f"force_coefficient '{recipe_name}': '{key}' must be a non-empty string")


def _require_positive_number(params: dict[str, Any], key: str, recipe_name: str) -> None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RecipeError(f"force_coefficient '{recipe_name}': '{key}' must be a positive number")


def _require_direction(value: Any, recipe_name: str) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RecipeError(f"force_coefficient '{recipe_name}': 'direction' must be a list of 3 numbers")
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise RecipeError(f"force_coefficient '{recipe_name}': 'direction' components must be numbers")
    norm_sq = sum(float(c) * float(c) for c in value)
    if norm_sq <= 0:
        raise RecipeError(f"force_coefficient '{recipe_name}': 'direction' must be a non-zero vector")


def _validate_aggregate_mode(mode: Any, recipe_name: str) -> None:
    if mode == "final":
        return
    if isinstance(mode, str):
        match = _AGGREGATE_PCT_RE.match(mode)
        if match:
            pct = int(match.group(1))
            if 0 < pct <= 100:
                return
    raise RecipeError(
        f"force_coefficient '{recipe_name}': 'aggregate' must be 'final' or 'mean_last_Npct' (0 < N <= 100)"
    )


# --- CSV / aggregation helpers ----------------------------------------------


def _read_force_csv(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        raise QoIError(f"force_coefficient: CSV not found: {path}")
    rows: list[dict[str, float]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise QoIError(f"force_coefficient: CSV has no header: {path}")
        missing = [col for col in _FORCE_COLUMNS if col not in reader.fieldnames]
        if missing:
            raise QoIError(f"force_coefficient: CSV {path} missing columns: {', '.join(missing)}")
        for raw in reader:
            try:
                rows.append({col: float(raw[col]) for col in _FORCE_COLUMNS})
            except (TypeError, ValueError) as exc:
                raise QoIError(f"force_coefficient: malformed row in {path}: {raw}") from exc
    if not rows:
        raise QoIError(f"force_coefficient: CSV has no data rows: {path}")
    return rows


def _aggregate_rows(rows: list[dict[str, float]], mode: str) -> dict[str, float]:
    if mode == "final":
        return dict(rows[-1])
    match = _AGGREGATE_PCT_RE.match(mode)
    if match is None:
        # validate() should have caught this, but be defensive
        raise QoIError(f"force_coefficient: unknown aggregate mode {mode!r}")
    pct = int(match.group(1))
    window = max(1, len(rows) * pct // 100)
    tail = rows[-window:]
    return {col: sum(r[col] for r in tail) / len(tail) for col in _FORCE_COLUMNS}


__all__ = [
    "ForceCoefficientExtractor",
    "helper_name",
    "output_csv_path",
    "render_helper",
]
