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
boundary = "wing"                 # required, str — name of the boundary zone
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
   pressure × normal and the wall stress vector (`boundary_stress` field) at
   every time step and appends a row to:
   ```
   caseXXXX/monitoring/csauto_forces_<boundary>.csv
   ```
   with columns `t, Fpx, Fpy, Fpz, Fvx, Fvy, Fvz`.
2. `postprocess` reads that CSV, aggregates over the requested time window,
   projects onto `direction` and normalizes by `0.5 * ref_density * ref_velocity^2 * ref_area`.

**Status caveat** — the C++ template is a **first draft**. It uses well-known
v9 symbols (`cs_boundary_zone_by_name_try`, `cs_field_by_name`, `cs_parall_sum`,
`cs_glob_time_step`, etc.) but has not yet been compiled and run against a real
code_saturne v9 install. Validation against a v9 Docker/Singularity image is
part of the next phase (test-compile at prepare). Until then, treat the template
as schema-correct but runtime-unverified.

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

## Roadmap

| Phase | What lands |
|---|---|
| 0 — skeleton ✅ | package layout, recipe parsing, doctor v9 gate, registry |
| 1 — first recipe ✅ | `force_coefficient` extractor + v9 C++ template + render() |
| 2 — `prepare` injection ✅ | per-boundary helpers assembled into `caseXXXX/SRC/cs_user_extra_operations.cpp`, mode `managed` |
| 3 — `csauto postprocess` CLI | walks RUNS, calls every extractor, exports CSV / parquet |
| 4 — test-compile at prepare | catch broken templates early on a single case using a v9 image |
| 5 — more recipes | `pressure_drop`, `heat_flux`, `field_stat`, `y_plus_stats` |
| 6 — coexistence mode | inject into a separate user-file when the template already ships its own |
| 7 — UI integration | dashboard panel with auto-discovery + interactive table + plots |

## Status of the gate

- `[[qoi]]` parsing: ✅ implemented
- `csauto doctor` version gate: ✅ implemented
- Built-in extractors: ✅ `force_coefficient` (1/N)
- C++ template injection at prepare: ✅ managed mode
- Postprocess CLI: ❌ not yet (phase 3)
- Test-compile at prepare: ❌ not yet (phase 4)
