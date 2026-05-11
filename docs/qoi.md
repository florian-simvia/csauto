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

## Roadmap

| Phase | What lands |
|---|---|
| 0 — skeleton (current) | package layout, recipe parsing, doctor v9 gate, registry |
| 1 — first recipe | `force_coefficient` end-to-end: C++ template (v9 API), `prepare` injection, extractor, parquet export |
| 2 — `csauto postprocess` CLI | `csauto postprocess RUNS --out results.parquet` |
| 3 — test-compile at prepare | catch broken templates early on a single case |
| 4 — more recipes | `pressure_drop`, `heat_flux`, `field_stat`, `y_plus_stats` |
| 5 — coexistence mode | inject into a separate user-file when the template already ships its own |
| 6 — UI integration | dashboard panel with auto-discovery + interactive table + plots |

## Status of the gate

- `[[qoi]]` parsing: ✅ implemented
- `csauto doctor` version gate: ✅ implemented
- Built-in extractors: ❌ none yet
- C++ template injection: ❌ not yet
- Postprocess CLI: ❌ not yet
