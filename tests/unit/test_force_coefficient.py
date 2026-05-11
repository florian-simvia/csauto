from __future__ import annotations

from pathlib import Path
from string import Template

import pytest

from csauto.qoi import Context, Recipe
from csauto.qoi.errors import QoIError, RecipeError
from csauto.qoi.types.force_coefficient import (
    ForceCoefficientExtractor,
    output_csv_path,
    render_template,
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
    csv_path = case_dir / output_csv_path(boundary)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    header = "t,Fpx,Fpy,Fpz,Fvx,Fvy,Fvz\n"
    body = "\n".join(",".join(f"{v}" for v in row) for row in rows)
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
            (0.001, 1, 0, 0, 1, 0, 0),  # transient noise, should be ignored
            (0.002, 2, 0, 0, 1, 0, 0),
            (0.003, 10, 0, 0, 2, 0, 0),  # final
        ],
    )
    ctx = Context(case_dir=case_dir)
    recipe = _make_recipe(aggregate="final")
    # q = 0.5 * 1 * 1^2 = 0.5; F_dir = 10 + 2 = 12; Cd = 12 / (0.5 * 1) = 24
    assert ForceCoefficientExtractor().extract(ctx, recipe) == {"Cd": pytest.approx(24.0)}


def test_extract_mean_last_10pct(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # 20 rows: 18 with Fpx=0, last 2 with Fpx=8 then Fpx=12 -> mean of last 2 = 10
    rows: list[tuple[float, ...]] = [(i * 0.001, 0, 0, 0, 0, 0, 0) for i in range(18)]
    rows.append((0.018, 8, 0, 0, 0, 0, 0))
    rows.append((0.019, 12, 0, 0, 0, 0, 0))
    _write_csv(case_dir, "wing", rows)
    ctx = Context(case_dir=case_dir)
    recipe = _make_recipe(aggregate="mean_last_10pct")
    # q=0.5, A=1, mean Fpx=10, Fvx=0 -> Cd = 10 / 0.5 = 20
    assert ForceCoefficientExtractor().extract(ctx, recipe) == {"Cd": pytest.approx(20.0)}


def test_extract_realistic_aerodynamics(tmp_path: Path) -> None:
    """Use realistic air-flow reference values; Cd should match closed-form result."""
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # Fp_x + Fv_x = 400 + 13.4375 = 413.4375
    # q = 0.5 * 1.225 * 30^2 = 551.25
    # Cd = 413.4375 / (551.25 * 1.5) = 0.5 exactly
    _write_csv(case_dir, "wing", [(0.1, 400.0, 0, 0, 13.4375, 0, 0)])
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
    _write_csv(case_dir, "wing", [(0.1, 10.0, 5.0, 2.0, 0, 0, 0)])
    ctx = Context(case_dir=case_dir)
    # direction y: F_dir = 5; q=0.5; Cl = 5 / 0.5 = 10
    recipe = _make_recipe(aggregate="final", direction=[0.0, 1.0, 0.0])
    object.__setattr__(recipe, "name", "Cl")
    result = ForceCoefficientExtractor().extract(ctx, recipe)
    assert result == {"Cl": pytest.approx(10.0)}


def test_extract_missing_csv_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="CSV not found"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


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
    csv_path.write_text("t,Fpx\n0.1,1.0\n", encoding="utf-8")
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="missing columns"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


def test_extract_malformed_row_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    csv_path = case_dir / output_csv_path("wing")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "t,Fpx,Fpy,Fpz,Fvx,Fvy,Fvz\n0.1,bogus,0,0,0,0,0\n",
        encoding="utf-8",
    )
    ctx = Context(case_dir=case_dir)
    with pytest.raises(QoIError, match="malformed row"):
        ForceCoefficientExtractor().extract(ctx, _make_recipe())


# --- C++ template rendering -------------------------------------------------


def test_output_csv_path_format() -> None:
    assert output_csv_path("wing") == "monitoring/csauto_forces_wing.csv"


def test_render_template_contains_boundary_and_output_path() -> None:
    rendered = render_template("wing")
    assert 'cs_boundary_zone_by_name_try("wing")' in rendered
    assert "monitoring/csauto_forces_wing.csv" in rendered


def test_render_template_has_no_unresolved_placeholders() -> None:
    rendered = render_template("wing")
    # string.Template uses $name / ${name} as placeholders. Any remaining
    # $-form would mean a missing substitution variable.
    leftovers = Template.pattern.findall(rendered)
    # Each match is a tuple; the placeholder name is the captured group.
    unresolved = [groups for groups in leftovers if any(group for group in groups[1:] if group)]
    assert unresolved == [], f"Unresolved placeholders: {unresolved}"


def test_render_template_for_different_boundaries_is_isolated() -> None:
    wing = render_template("wing")
    fuselage = render_template("fuselage")
    assert "wing" in wing
    assert "fuselage" in fuselage
    assert "fuselage" not in wing
    assert "wing" not in fuselage
