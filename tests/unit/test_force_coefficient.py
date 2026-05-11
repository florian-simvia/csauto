from __future__ import annotations

from pathlib import Path
from string import Template

import pytest

from csauto.qoi import Context, Recipe
from csauto.qoi.errors import QoIError, RecipeError
from csauto.qoi.types.force_coefficient import (
    ForceCoefficientExtractor,
    helper_name,
    output_csv_path,
    render_helper,
)

# --- Test helpers -----------------------------------------------------------


def _make_recipe(**overrides: object) -> Recipe:
    """Build a force_coefficient recipe with sensible defaults, overridable."""
    params: dict[str, object] = {
        "boundary": "wing",
        "direction": [1.0, 0.0, 0.0],
        "ref_area": 1.0,
        "ref_velocity": 1.0,
        "ref_density": 1.0,
    }
    params.update(overrides)
    return Recipe(name="Cd", type="force_coefficient", params=params)


def _write_csv(case_dir: Path, boundary: str, rows: list[tuple[float, ...]]) -> Path:
    """Write a CSV in the v9 schema: t, Fx, Fy, Fz, Fn, Ftx, Fty, Ftz.

    Each row is the time followed by 7 numbers: total force (3), integrated
    normal stress magnitude (1, scalar), tangential force (3). Tests that
    only care about the total can pass 4-tuples (t, Fx, Fy, Fz) — the helper
    zero-pads the diagnostic columns.
    """
    csv_path = case_dir / output_csv_path(boundary)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = "t,Fx,Fy,Fz,Fn,Ftx,Fty,Ftz\n"
    padded_rows = []
    for row in rows:
        if len(row) == 4:  # t, Fx, Fy, Fz
            padded_rows.append((*row, 0, 0, 0, 0))
        elif len(row) == 8:
            padded_rows.append(row)
        else:
            raise ValueError(f"_write_csv expects 4- or 8-tuples, got {len(row)}")
    body = "\n".join(",".join(f"{v}" for v in row) for row in padded_rows)
    csv_path.write_text(header + body + ("\n" if body else ""), encoding="utf-8")
    return csv_path


# --- validate() -------------------------------------------------------------


def test_validate_happy_path() -> None:
    ForceCoefficientExtractor().validate(_make_recipe())


def test_validate_accepts_explicit_aggregate_modes() -> None:
    ext = ForceCoefficientExtractor()
    for mode in ("final", "mean_last_1pct", "mean_last_10pct", "mean_last_100pct"):
        ext.validate(_make_recipe(aggregate=mode))


def test_validate_rejects_missing_boundary() -> None:
    recipe = _make_recipe()
    object.__setattr__(recipe, "params", {k: v for k, v in recipe.params.items() if k != "boundary"})
    with pytest.raises(RecipeError, match="'boundary' must be a non-empty string"):
        ForceCoefficientExtractor().validate(recipe)


def test_validate_rejects_blank_boundary() -> None:
    with pytest.raises(RecipeError, match="'boundary' must be a non-empty string"):
        ForceCoefficientExtractor().validate(_make_recipe(boundary="   "))


@pytest.mark.parametrize(
    "direction",
    [
        None,
        [1.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        "not a list",
        [1.0, "x", 0.0],
        [True, False, False],
        [0.0, 0.0, 0.0],
    ],
)
def test_validate_rejects_bad_direction(direction: object) -> None:
    with pytest.raises(RecipeError, match="direction"):
        ForceCoefficientExtractor().validate(_make_recipe(direction=direction))


@pytest.mark.parametrize("key", ["ref_area", "ref_velocity", "ref_density"])
@pytest.mark.parametrize("bad_value", [0, -1.5, "1.5", True, None])
def test_validate_rejects_non_positive_ref(key: str, bad_value: object) -> None:
    with pytest.raises(RecipeError, match=key):
        ForceCoefficientExtractor().validate(_make_recipe(**{key: bad_value}))


@pytest.mark.parametrize(
    "bad_mode",
    ["mean_last_0pct", "mean_last_200pct", "average", "", "mean_last_pct"],
)
def test_validate_rejects_unknown_aggregate(bad_mode: str) -> None:
    with pytest.raises(RecipeError, match="aggregate"):
        ForceCoefficientExtractor().validate(_make_recipe(aggregate=bad_mode))


# --- extract() --------------------------------------------------------------


def test_extract_final_aggregate(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    _write_csv(
        case_dir,
        "wing",
        [
            (0.001, 2, 0, 0),  # transient noise, should be ignored
            (0.002, 3, 0, 0),
            (0.003, 12, 0, 0),  # final: total Fx = 12
        ],
    )
    ctx = Context(case_dir=case_dir)
    recipe = _make_recipe(aggregate="final")
    # q = 0.5 * 1 * 1^2 = 0.5; F_dir = 12; Cd = 12 / (0.5 * 1) = 24
    assert ForceCoefficientExtractor().extract(ctx, recipe) == {"Cd": pytest.approx(24.0)}


def test_extract_mean_last_10pct(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # 20 rows: 18 with Fx=0, last 2 with Fx=8 then Fx=12 -> mean of last 2 = 10
    rows: list[tuple[float, ...]] = [(i * 0.001, 0, 0, 0) for i in range(18)]
    rows.append((0.018, 8, 0, 0))
    rows.append((0.019, 12, 0, 0))
    _write_csv(case_dir, "wing", rows)
    ctx = Context(case_dir=case_dir)
    recipe = _make_recipe(aggregate="mean_last_10pct")
    # q=0.5, A=1, mean Fx=10 -> Cd = 10 / 0.5 = 20
    assert ForceCoefficientExtractor().extract(ctx, recipe) == {"Cd": pytest.approx(20.0)}


def test_extract_realistic_aerodynamics(tmp_path: Path) -> None:
    """Use realistic air-flow reference values; Cd should match closed-form result."""
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # Total Fx = 413.4375
    # q = 0.5 * 1.225 * 30^2 = 551.25
    # Cd = 413.4375 / (551.25 * 1.5) = 0.5 exactly
    _write_csv(case_dir, "wing", [(0.1, 413.4375, 0, 0)])
    ctx = Context(case_dir=case_dir)
    recipe = _make_recipe(
        aggregate="final",
        ref_area=1.5,
        ref_velocity=30.0,
        ref_density=1.225,
    )
    result = ForceCoefficientExtractor().extract(ctx, recipe)
    assert result == {"Cd": pytest.approx(0.5)}


def test_extract_projects_on_direction(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    _write_csv(case_dir, "wing", [(0.1, 10.0, 5.0, 2.0)])
    ctx = Context(case_dir=case_dir)
    # direction y: F_dir = 5; q=0.5; Cl = 5 / 0.5 = 10
    recipe = _make_recipe(aggregate="final", direction=[0.0, 1.0, 0.0])
    object.__setattr__(recipe, "name", "Cl")
    result = ForceCoefficientExtractor().extract(ctx, recipe)
    assert result == {"Cl": pytest.approx(10.0)}


def test_extract_ignores_diagnostic_normal_tangential_columns(tmp_path: Path) -> None:
    """Total Fx drives the coefficient; Fn (scalar) and Ft* travel for diagnostics but must not pollute."""
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # Full 8-column row: total = (12, 0, 0); normal magnitude = 10; tangential = (2, 0, 0)
    _write_csv(case_dir, "wing", [(0.1, 12.0, 0, 0, 10.0, 2.0, 0, 0)])
    ctx = Context(case_dir=case_dir)
    # q=0.5; Cd should use total Fx=12 -> 24, not normal (20) nor tangential (4)
    assert ForceCoefficientExtractor().extract(ctx, _make_recipe(aggregate="final")) == {"Cd": pytest.approx(24.0)}


def test_extract_missing_csv_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="no RESU run found"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


def test_extract_csv_missing_in_existing_resu_raises(tmp_path: Path) -> None:
    """A RESU run that didn't produce the force CSV yields a targeted error."""
    case_dir = tmp_path / "case0001"
    (case_dir / "RESU" / "20260101-1200" / "monitoring").mkdir(parents=True)
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="CSV not found for boundary 'wing'"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


def test_extract_finds_csv_in_latest_resu(tmp_path: Path) -> None:
    """Falls back to looking inside RESU/<latest>/monitoring/ when present."""
    case_dir = tmp_path / "case0001"
    monitoring = case_dir / "RESU" / "20260101-1200" / "monitoring"
    monitoring.mkdir(parents=True)
    (monitoring / "csauto_forces_wing.csv").write_text(
        "t,Fx,Fy,Fz,Fn,Ftx,Fty,Ftz\n0.1,12.0,0,0,-12.0,0,0,0\n",
        encoding="utf-8",
    )
    ctx = Context(case_dir=case_dir)
    result = ForceCoefficientExtractor().extract(ctx, _make_recipe(aggregate="final"))
    assert result == {"Cd": pytest.approx(24.0)}


def test_extract_header_only_csv_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    _write_csv(case_dir, "wing", [])
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="no data rows"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


def test_extract_missing_columns_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    csv_path = case_dir / output_csv_path("wing")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("t,Fx\n0.1,1.0\n", encoding="utf-8")
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="missing columns"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


def test_extract_malformed_row_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    csv_path = case_dir / output_csv_path("wing")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "t,Fx,Fy,Fz\n0.1,bogus,0,0\n",
        encoding="utf-8",
    )
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="malformed row"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


# --- C++ template rendering -------------------------------------------------


def test_output_csv_path_format() -> None:
    assert output_csv_path("wing") == "monitoring/csauto_forces_wing.csv"


def test_helper_name_basic() -> None:
    assert helper_name("wing") == "csauto_qoi_force_wing"


@pytest.mark.parametrize(
    ("boundary", "expected"),
    [
        ("wing-tip", "csauto_qoi_force_wing_tip"),
        ("wing.upper", "csauto_qoi_force_wing_upper"),
        ("wing/lower", "csauto_qoi_force_wing_lower"),
        ("1stbody", "csauto_qoi_force__1stbody"),
    ],
)
def test_helper_name_sanitizes_non_identifier_characters(boundary: str, expected: str) -> None:
    assert helper_name(boundary) == expected


def test_render_helper_emits_static_function_with_boundary_and_output_path() -> None:
    rendered = render_helper("wing")
    assert rendered.lstrip().startswith("static void")
    assert "csauto_qoi_force_wing(cs_domain_t *domain)" in rendered
    assert 'cs_boundary_zone_by_name_try("wing")' in rendered
    assert "monitoring/csauto_forces_wing.csv" in rendered


def test_render_helper_has_no_unresolved_placeholders() -> None:
    rendered = render_helper("wing")
    # string.Template uses $name / ${name} as placeholders. Any remaining
    # $-form would mean a missing substitution variable.
    leftovers = Template.pattern.findall(rendered)
    unresolved = [groups for groups in leftovers if any(group for group in groups[1:] if group)]
    assert unresolved == [], f"Unresolved placeholders: {unresolved}"


def test_render_helper_for_different_boundaries_is_isolated() -> None:
    wing = render_helper("wing")
    fuselage = render_helper("fuselage")
    assert "wing" in wing
    assert "fuselage" in fuselage
    assert "fuselage" not in wing
    assert "wing" not in fuselage


def test_cpp_helpers_returns_one_helper_per_boundary() -> None:
    recipe = _make_recipe()  # boundary = "wing"
    helpers = ForceCoefficientExtractor().cpp_helpers(recipe)
    assert len(helpers) == 1
    assert helpers[0].name == "csauto_qoi_force_wing"
    assert "csauto_qoi_force_wing" in helpers[0].code
    assert "monitoring/csauto_forces_wing.csv" in helpers[0].code
