# QoI auto-postprocessing

> **Status:** skeleton (v0.1). Recipe parsing and version gating only — no extractor is shipped yet. See the roadmap section at the bottom for what's coming.

## What is it

A recipe-driven system that automatically extracts **Quantities of Interest** (drag/lift coefficients, pressure drops, wall heat fluxes, custom integrals, …) from every case in a campaign and assembles a single pandas-ready table joining the DOE inputs with the computed outputs.

Goal: replace the ad-hoc post-processing scripts every CFD engineer rewrites for every project.

## Requirements

| Feature | Required code_saturne version |
|---|---|
| csauto core (prepare, run, dashboard, …) | any |
| QoI auto-postprocessing | **>= 9.0** |

Most recipes need code_saturne to compute integrated quantities that are not part of the default output (e.g. pressure + viscous force integration on a boundary). csauto generates the corresponding `cs_user_extra_operations.cpp` automatically using the **v9 user-file API**.

Older versions of code_saturne use a different (and partly incompatible) user-file API. We deliberately do **not** support them for QoI extraction — csauto is recent enough that targeting only v9+ is a defensible trade-off. Multi-version support is not a goal.

`csauto doctor` enforces this gate: if `[[qoi]]` entries are declared in your config but the detected code_saturne version is < 9, the check fails with an explicit message. If no recipes are configured, doctor does not even attempt the version detection — your campaigns are unaffected.

## Recipe schema

Recipes live in `csauto.toml` as a TOML array of tables:

```toml
[[qoi]]
name = "Cd"                          # column name in the result table
type = "force_coefficient"           # extractor identifier
boundary = "wing"                    # type-specific param
direction = [1, 0, 0]
ref_area = 1.5
ref_velocity = 30.0
ref_density = 1.225

[[qoi]]
name = "dP"
type = "pressure_drop"
probe_in = "inlet"
probe_out = "outlet"
```

Common fields (parsed and validated today):
- `name` — non-empty, unique within the file.
- `type` — non-empty; must eventually match a registered extractor.

Any other key/value is forwarded as type-specific parameters to the extractor. Schema validation per type lives in the extractor and is enforced when the recipe is actually used.

## Architecture (skeleton)

```
csauto/qoi/
├── __init__.py        ← public API
├── errors.py          ← QoIError, RecipeError, UnknownRecipeTypeError, SaturneVersionError
├── version.py         ← MIN_SATURNE_VERSION = (9, 0) + detect/parse helpers
├── recipe.py          ← Recipe dataclass + parse_recipe()
├── registry.py        ← @register decorator + get_extractor / available_types
└── extractor.py       ← Extractor Protocol + Context
```

The contract for an extractor is intentionally narrow:

```python
class Extractor(Protocol):
    def validate(self, recipe: Recipe) -> None: ...
    def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]: ...
```

Future extensions (already anticipated):
- `cpp_template() -> Path` — for recipes that require csauto to inject a user file at `prepare` time.
- `cache_key(recipe) -> str` — for incremental recomputation.

## Recipe types

### `force_coefficient`

Compute an aerodynamic-style coefficient (Cd, Cl, Cm, ...) by integrating
pressure and viscous forces over a boundary patch.

```toml
[[qoi]]
name = "Cd"                       # column name in the result table
type = "force_coefficient"
boundary = "wing"                 # required, str — boundary zone LABEL in setup.xml
direction = [1.0, 0.0, 0.0]       # required, list[float] length 3 — projection axis
ref_area = 1.5                    # required, float > 0
ref_velocity = 30.0               # required, float > 0
ref_density = 1.225               # required, float > 0
aggregate = "mean_last_10pct"     # optional, default "mean_last_10pct"
```

**Aggregate modes**
- `"final"` — take the last sample only
- `"mean_last_Npct"` — mean over the last N% of samples (e.g. `mean_last_10pct`, `mean_last_50pct`)

**Pipeline**
1. `prepare` writes `caseXXXX/SRC/cs_user_extra_operations.cpp` from a template
   that targets the **code_saturne v9** user-file API. The user file integrates
   three boundary fields — `Stress` (total wall traction), `stress_normal` and
   `stress_tangential` — over the named boundary at every time step, and appends
   one row to:
   ```
   caseXXXX/monitoring/csauto_forces_<boundary>.csv
   ```
   with columns:
   ```
   t, Fx, Fy, Fz, Fnx, Fny, Fnz, Ftx, Fty, Ftz
   ```
   `Fx, Fy, Fz` are the components of the total integrated force. The `Fn*` and
   `Ft*` columns carry the normal and tangential decomposition for diagnostic
   use (form drag vs skin friction); the extractor reads them but only uses the
   total to compute the coefficient.
2. `postprocess` reads that CSV, aggregates over the requested time window,
   projects onto `direction` and normalizes by `0.5 * ref_density * ref_velocity^2 * ref_area`.

**Boundary name gotcha** — `boundary` must be the **zone label** as it appears
in `<boundary label="...">` in setup.xml (the human-friendly name attached to
the zone), not the geometric *selection criterion* (groups like `"WALL_TOP"` or
`"INLET_left"`) nor the mesh-side group name. If the C++ helper cannot find the
zone, it prints once:
```
[csauto] force_coefficient: boundary zone "X" not found. Check setup.xml —
the name must match the zone label (not a geom selection criterion / group).
Skipping.
```
and no force CSV is produced for that boundary.

**Required setup activation (v9)** — `force_coefficient` needs the boundary
property `stress` (the wall traction vector σ·n) recorded by code_saturne.
**csauto activates it automatically** at `prepare` time by toggling
`<postprocessing_recording status="off"/>` to `"on"` inside the matching
`<property name="stress">` block of every `caseXXXX/DATA/setup.xml`. There is
no manual click in the code_saturne GUI to do.

The normal and tangential components written to the CSV (`Fn`, `Ftx/y/z`) are
**computed by the C++ helper from `boundary_stress` and the local face normal**,
not read from code_saturne's `stress_normal`/`stress_tangential` post-processing
fields (which are not exposed in the runtime field registry as of v9).

If `<property name="stress">` is missing from your template setup.xml entirely,
csauto prints a warning during `prepare` pointing to the impacted case — that
case will not produce a force CSV at runtime. Add the missing `<property>` to
your template setup and re-run prepare.

If the user later disables the field by hand (e.g. by re-saving the setup with
the GUI), the C++ helper detects the missing field at runtime, prints a single
stderr warning identifying the boundary, and skips silently. The CSV will not
be created and `postprocess` will fail with a clear "CSV not found" error.

**Units assumption** — the helper assumes the three stress fields are wall
tractions expressed in Pa (N/m²) and multiplies by `b_face_surf` to obtain
Newtons. If a v9 release exposes these fields as already-integrated forces
(N per face), one inner-loop line of the template needs to drop the `area`
factor; let us know if your runtime coefficients come out off by an area
factor.

**Status caveat** — the C++ template is a **first draft**. It uses
plausible v9 symbols (`cs_boundary_zone_by_name_try`, `cs_field_by_name_try`,
`cs_parall_sum`, `cs_glob_time_step`, ...) but has not yet been compiled and
run against a real code_saturne v9 install. Validation against a v9
Docker/Singularity image is part of the next phase (test-compile at prepare).
Until then, treat the template as schema-correct but runtime-unverified.

## How injection works

When `csauto prepare` runs and at least one `[[qoi]]` recipe is declared, csauto:

1. Generates each `caseXXXX/` directory normally (template copy + DOE rendering).
2. For each recipe, asks the matching extractor for the C++ helpers it needs
   (`cpp_helpers(recipe)`). Helpers are keyed by name — two recipes targeting
   the same boundary share one helper (Cd and Cl on "wing" → one helper, one
   force CSV, two coefficients).
3. Assembles a single `cs_user_extra_operations.cpp` containing every helper as
   a `static` function and a single entry point dispatching to each. The order
   is deterministic (alphabetical by helper name) so the file is reproducible.
4. Writes it to `caseXXXX/SRC/cs_user_extra_operations.cpp`. code_saturne picks
   it up automatically at run time.

### Managed mode only (for now)

csauto operates in **managed mode**: it assumes nothing else owns
`SRC/cs_user_extra_operations.*`. If a user-authored file with the same basename
is present (any of `.cpp`, `.cxx`, `.cc`, `.c`), `prepare` refuses to overwrite
it and stops. The error points to the offending case. To proceed you must either:
- Move your custom file aside (and accept losing the QoI feature for that case), or
- Remove `[[qoi]]` from your config, or
- Wait for the `injected` coexistence mode (later phase).

csauto-generated files are recognized by an `auto-generated by csauto` marker in
their header, so re-running `prepare` refreshes them in place without
complaining.

## `csauto postprocess`

Walks every `caseXXXX/` under `RUNS/`, applies every configured `[[qoi]]`
recipe and writes a single campaign-level table.

```bash
csauto postprocess RUNS --out results.csv          # CSV (default if .csv)
csauto postprocess RUNS --out results.tsv          # TSV
csauto postprocess RUNS --out results.json         # JSON list of objects
csauto postprocess RUNS --out results.dat --format csv   # override
csauto postprocess RUNS --out results.csv --cases case0001,case0042  # subset
```

The CSV/TSV/JSON exports are stdlib-only — no pandas dependency in csauto's
core. Use any pandas/Polars/R/Excel toolchain downstream:

```python
import pandas as pd
df = pd.read_csv("results.csv")
df.plot.scatter(x="u_inlet", y="Cd")
```

### Output schema

| Column | Source |
|---|---|
| `case_id` | case directory name |
| each DOE column | from `caseXXXX/doe_row.csv` (minus the duplicated `case_id`) |
| each QoI column | extractor result keyed by `recipe.name` |
| `_status` | `"ok"` if every recipe ran cleanly, `"error"` otherwise |
| `_errors` | semicolon-separated error messages (empty when `_status == "ok"`) |

Column ordering is stable: DOE columns in first-seen order across cases,
then QoI columns in recipe declaration order. Empty CSV cell / JSON `null`
when a recipe failed for a given case — pandas reads it as `NaN`, so the
table stays usable even when some cases didn't converge or produced no
output.

### Failure handling

- Missing `doe_row.csv` → row has empty DOE cells, no error logged
- Missing force CSV (case didn't run, runtime warning, etc.) → that QoI
  cell is `NaN`, error like `"Cd: force_coefficient: CSV not found: ..."`
  appears in `_errors`
- Unknown recipe type (typo in config) → all rows get an error for that
  recipe, valid recipes still extract normally
- Unexpected exception inside an extractor → logged with class + message,
  whole campaign continues

## `csauto prepare --test-compile`

Opt-in flag on `prepare` that validates the auto-generated user file against
the configured code_saturne runtime **before** you spend any CPU on the
campaign. Picks the first generated case and runs:

```
code_saturne compile -t -s SRC
```

inside it. `-t` is "test mode" — no object files are kept on disk, only the
exit code matters. On success:

```
$ csauto prepare doe.csv TEMPLATE RUNS --test-compile
Injected QoI user file in 12 case(s)
Enabled QoI boundary fields in setup.xml for 12 case(s)
Test-compiling first case with runtime=docker image=simvia/code_saturne:9.1.0...
✓ test-compile OK (docker) for case0001
```

On failure the full `mpic++` output is dumped to stderr and prepare exits 1 —
the cases stay on disk so you can inspect what was generated:

```
$ csauto prepare doe.csv TEMPLATE RUNS --test-compile
...
✗ test-compile FAILED (docker) for case0001
--- compile log -----------------------------
.../cs_user_extra_operations.cpp:42:6: error: 'cs_field_by_name_typo' was not declared
---------------------------------------------
Error: Test-compile failed (exit code 1). Fix the issue in the auto-generated
user file or your template, then re-run prepare. The cases on disk are left untouched.
```

### Runtime support

| Runtime | Status |
|---|---|
| `docker` | ✅ pulls the configured `docker_image`, mounts the parent runs dir |
| `singularity` / `apptainer` | ✅ binds the parent runs dir into the image |
| `native` | ✅ invokes the resolved `code_saturne` binary directly |

The chosen runtime mirrors what `csauto run` will use. Default timeout is
300 s (configurable in code, not yet exposed as a CLI flag).

### When to use it

- After modifying your template's `cs_user_*.cpp` files
- After upgrading code_saturne (catches API breakages)
- After editing the `[[qoi]]` section (typo on a boundary name etc.)
- In CI before a release tag

Skipping `--test-compile` is fine for routine reruns — the auto-generated
file is deterministic and only changes when the recipe set changes.

## Roadmap

| Phase | What lands |
|---|---|
| 0 — skeleton ✅ | package layout, recipe parsing, doctor v9 gate, registry |
| 1 — first recipe ✅ | `force_coefficient` extractor + v9 C++ template + render() |
| 2 — `prepare` injection ✅ | per-boundary helpers assembled into `caseXXXX/SRC/cs_user_extra_operations.cpp`, mode `managed` |
| 3 — `csauto postprocess` CLI ✅ | walks RUNS, runs every extractor, writes CSV/TSV/JSON table |
| 4 — test-compile at prepare ✅ | `--test-compile` flag invokes `code_saturne compile -t` on the first case via the configured runtime |
| 5 — more recipes | `pressure_drop`, `heat_flux`, `field_stat`, `y_plus_stats` |
| 6 — coexistence mode | inject into a separate user-file when the template already ships its own |
| 7 — UI integration | dashboard panel with auto-discovery + interactive table + plots |

## Status of the gate

- `[[qoi]]` parsing: ✅ implemented
- `csauto doctor` version gate: ✅ implemented
- Built-in extractors: ✅ `force_coefficient` (1/N)
- C++ template injection at prepare: ✅ managed mode
- Postprocess CLI: ✅ CSV / TSV / JSON
- Test-compile at prepare: ✅ `--test-compile` flag
