from __future__ import annotations

from pathlib import Path
from string import Template

import pytest

from csauto.qoi import Context, Recipe
from csauto.qoi.errors import QoIError, RecipeError
from csauto.qoi.types.pressure_drop import (
    PressureDropExtractor,
    helper_name,
    output_csv_path,
    render_helper,
)

# --- Test helpers -----------------------------------------------------------


def _make_recipe(**overrides: object) -> Recipe:
    params: dict[str, object] = {
        "inlet": "INLET",
        "outlet": "OUTLET",
        "mode": "bernoulli",
    }
    params.update(overrides)
    return Recipe(name="dP", type="pressure_drop", params=params)


def _write_csv(
    case_dir: Path,
    boundary: str,
    rows: list[tuple[float, float, float, float, float]],
) -> Path:
    """Each row: (t, p_avg, ke_avg, gh_avg, area)."""
    path = case_dir / output_csv_path(boundary)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "t,p_avg,ke_avg,gh_avg,area\n"
    body = "\n".join(",".join(f"{v}" for v in row) for row in rows)
    path.write_text(header + body + "\n", encoding="utf-8")
    return path


# --- validate() -------------------------------------------------------------


def test_validate_happy_path() -> None:
    PressureDropExtractor().validate(_make_recipe())


def test_validate_default_mode_is_bernoulli() -> None:
    recipe = Recipe(name="dP", type="pressure_drop", params={"inlet": "I", "outlet": "O"})
    PressureDropExtractor().validate(recipe)  # no mode key → bernoulli default


def test_validate_explicit_static_mode_ok() -> None:
    PressureDropExtractor().validate(_make_recipe(mode="static"))


def test_validate_rejects_unknown_mode() -> None:
    with pytest.raises(RecipeError, match="'mode' must be one of"):
        PressureDropExtractor().validate(_make_recipe(mode="exotic"))


@pytest.mark.parametrize("key", ["inlet", "outlet"])
def test_validate_rejects_missing_or_blank_boundary(key: str) -> None:
    with pytest.raises(RecipeError, match=f"'{key}' must be a non-empty string"):
        PressureDropExtractor().validate(_make_recipe(**{key: "  "}))


def test_validate_rejects_same_inlet_outlet() -> None:
    with pytest.raises(RecipeError, match="must reference different boundaries"):
        PressureDropExtractor().validate(_make_recipe(inlet="X", outlet="X"))


def test_validate_rejects_unknown_aggregate() -> None:
    with pytest.raises(RecipeError, match="aggregate"):
        PressureDropExtractor().validate(_make_recipe(aggregate="bogus"))


# --- cpp_helpers / required_setup_properties --------------------------------


def test_cpp_helpers_returns_two_per_recipe() -> None:
    helpers = PressureDropExtractor().cpp_helpers(_make_recipe())
    names = sorted(h.name for h in helpers)
    assert names == [
        "csauto_qoi_pressure_INLET",
        "csauto_qoi_pressure_OUTLET",
    ]


def test_required_setup_properties_lists_total_pressure() -> None:
    assert PressureDropExtractor().required_setup_properties(_make_recipe()) == ["total_pressure"]


def test_helper_name_sanitization() -> None:
    assert helper_name("WALL-IN.foo") == "csauto_qoi_pressure_WALL_IN_foo"


def test_render_helper_contains_boundary_and_csv_path() -> None:
    src = render_helper("INLET")
    assert "csauto_qoi_pressure_INLET" in src
    assert 'cs_boundary_zone_by_name_try("INLET")' in src
    assert "monitoring/csauto_pressure_INLET.csv" in src


def test_render_helper_has_no_unresolved_placeholders() -> None:
    src = render_helper("OUTLET")
    leftovers = Template.pattern.findall(src)
    unresolved = [g for g in leftovers if any(group for group in g[1:] if group)]
    assert unresolved == []


# --- extract() — happy paths ------------------------------------------------


def test_extract_static_mode(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    _write_csv(case_dir, "INLET", [(0.1, 101_325.0, 0.0, 0.0, 1.0)])
    _write_csv(case_dir, "OUTLET", [(0.1, 100_325.0, 200.0, 100.0, 1.0)])
    recipe = _make_recipe(mode="static", aggregate="final")
    result = PressureDropExtractor().extract(Context(case_dir=case_dir), recipe)
    # static mode ignores ke and gh entirely
    assert result == {"dP": pytest.approx(1000.0)}


def test_extract_bernoulli_includes_kinetic_and_gravity(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    _write_csv(case_dir, "INLET", [(0.1, 101_325.0, 500.0, 0.0, 1.0)])
    _write_csv(case_dir, "OUTLET", [(0.1, 100_325.0, 200.0, 100.0, 1.0)])
    recipe = _make_recipe(mode="bernoulli", aggregate="final")
    result = PressureDropExtractor().extract(Context(case_dir=case_dir), recipe)
    # H_in  = 101325 + 500 +   0 = 101_825
    # H_out = 100325 + 200 + 100 = 100_625
    # dP = 1200
    assert result == {"dP": pytest.approx(1200.0)}


def test_extract_bernoulli_static_fluid_yields_zero_head_loss(tmp_path: Path) -> None:
    """A static fluid with two sections at different heights must have dH_loss = 0.

    p_static drops with elevation (dp_static = -rhogdh), but the gravity head
    column rhogz reconstructs the same magnitude → bernoulli mode returns 0.
    """
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # rho=1000, g=9.81, dz = 10m: static drop = 98_100, gh restitues = 98_100
    _write_csv(case_dir, "INLET", [(0.1, 101_325.0, 0.0, 0.0, 1.0)])
    _write_csv(case_dir, "OUTLET", [(0.1, 3_225.0, 0.0, 98_100.0, 1.0)])
    recipe = _make_recipe(mode="bernoulli", aggregate="final")
    result = PressureDropExtractor().extract(Context(case_dir=case_dir), recipe)
    assert result == {"dP": pytest.approx(0.0, abs=1e-6)}


def test_extract_mean_aggregate(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # 10 rows on each side; last 1 (10pct) is what counts
    rows_in = [(i * 0.01, 100_000 + i, 0, 0, 1.0) for i in range(10)]
    rows_out = [(i * 0.01, 99_000 + i, 0, 0, 1.0) for i in range(10)]
    _write_csv(case_dir, "INLET", rows_in)
    _write_csv(case_dir, "OUTLET", rows_out)
    recipe = _make_recipe(mode="static", aggregate="mean_last_10pct")
    # mean of last 1 sample on each side: (100_009) - (99_009) = 1000
    result = PressureDropExtractor().extract(Context(case_dir=case_dir), recipe)
    assert result == {"dP": pytest.approx(1000.0)}


# --- extract() — error paths ------------------------------------------------


def test_extract_missing_inlet_csv_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    (case_dir / "RESU" / "20260101-1200" / "monitoring").mkdir(parents=True)
    # Only outlet, no inlet
    _write_csv(
        case_dir / "RESU" / "20260101-1200",
        "OUTLET",
        [(0.1, 100_000.0, 0.0, 0.0, 1.0)],
    )
    with pytest.raises(QoIError, match="CSV not found for boundary 'INLET'"):
        PressureDropExtractor().extract(Context(case_dir=case_dir), _make_recipe())


def test_extract_no_resu_dir_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    with pytest.raises(QoIError, match="no RESU run found"):
        PressureDropExtractor().extract(Context(case_dir=case_dir), _make_recipe())


def test_extract_csv_missing_columns_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    # Inlet has full schema, outlet missing gh_avg
    _write_csv(case_dir, "INLET", [(0.1, 100_000.0, 0.0, 0.0, 1.0)])
    out = case_dir / output_csv_path("OUTLET")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("t,p_avg,ke_avg,area\n0.1,99000,0,1\n", encoding="utf-8")
    with pytest.raises(QoIError, match="missing columns"):
        PressureDropExtractor().extract(Context(case_dir=case_dir), _make_recipe())


def test_extract_finds_csv_in_latest_resu(tmp_path: Path) -> None:
    """Same convention as force_coefficient — pulls from RESU/<latest>/monitoring/."""
    case_dir = tmp_path / "case0001"
    monitoring = case_dir / "RESU" / "20260101-1300"
    monitoring.mkdir(parents=True)
    _write_csv(monitoring, "INLET", [(0.1, 101_000.0, 0.0, 0.0, 1.0)])
    _write_csv(monitoring, "OUTLET", [(0.1, 100_000.0, 0.0, 0.0, 1.0)])
    recipe = _make_recipe(mode="static", aggregate="final")
    result = PressureDropExtractor().extract(Context(case_dir=case_dir), recipe)
    assert result == {"dP": pytest.approx(1000.0)}
