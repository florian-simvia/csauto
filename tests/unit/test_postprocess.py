from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from csauto.qoi import Recipe
from csauto.qoi.errors import QoIError
from csauto.qoi.postprocess import (
    CaseResult,
    doe_column_order,
    extract_case_qois,
    extract_runs_qois,
    qoi_column_order,
    write_table,
)

# --- Test helpers -----------------------------------------------------------


def _force_recipe(name: str = "Cd", boundary: str = "wing") -> Recipe:
    return Recipe(
        name=name,
        type="force_coefficient",
        params={
            "boundary": boundary,
            "direction": [1.0, 0.0, 0.0],
            "ref_area": 1.0,
            "ref_velocity": 1.0,
            "ref_density": 1.0,
            "aggregate": "final",
        },
    )


def _make_case(
    runs_dir: Path,
    case_id: str,
    doe_values: dict[str, str] | None = None,
    boundary_force: tuple[str, float] | None = None,
) -> Path:
    """Build a fake case with optional DOE row and force CSV."""
    case_dir = runs_dir / case_id
    case_dir.mkdir(parents=True)
    if doe_values is not None:
        header = ",".join(["case_id", *doe_values.keys()])
        values = ",".join([case_id, *(str(v) for v in doe_values.values())])
        (case_dir / "doe_row.csv").write_text(header + "\n" + values + "\n", encoding="utf-8")
    if boundary_force is not None:
        boundary, fx = boundary_force
        monitoring = case_dir / "monitoring"
        monitoring.mkdir()
        (monitoring / f"csauto_forces_{boundary}.csv").write_text(
            "t,Fx,Fy,Fz,Fn,Ftx,Fty,Ftz\n0.1," + f"{fx}" + ",0,0,0,0,0,0\n",
            encoding="utf-8",
        )
    return case_dir


# --- extract_case_qois ------------------------------------------------------


def test_extract_case_qois_happy_path(tmp_path: Path) -> None:
    case = _make_case(tmp_path, "case0001", {"u_inlet": "30.0"}, ("wing", 12.0))
    result = extract_case_qois(case, [_force_recipe()])

    assert result.case_id == "case0001"
    assert result.doe_row["u_inlet"] == "30.0"
    # q = 0.5, F = 12 -> Cd = 24
    assert result.qois == {"Cd": pytest.approx(24.0)}
    assert result.errors == []
    assert result.status == "ok"


def test_extract_case_qois_records_failure_per_recipe(tmp_path: Path) -> None:
    case = _make_case(tmp_path, "case0001", {"u_inlet": "30.0"})  # no force CSV
    result = extract_case_qois(case, [_force_recipe()])

    assert math.isnan(result.qois["Cd"])
    assert len(result.errors) == 1
    assert "no RESU run found" in result.errors[0]
    assert result.status == "error"


def test_extract_case_qois_unknown_recipe_type(tmp_path: Path) -> None:
    case = _make_case(tmp_path, "case0001", {"u_inlet": "30.0"}, ("wing", 12.0))
    bogus = Recipe(name="Bogus", type="does_not_exist")
    result = extract_case_qois(case, [bogus, _force_recipe()])

    assert math.isnan(result.qois["Bogus"])
    assert result.qois["Cd"] == pytest.approx(24.0)
    assert any("Unknown recipe type" in e for e in result.errors)


def test_extract_case_qois_unexpected_exception_does_not_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _make_case(tmp_path, "case0001", {"u_inlet": "30.0"}, ("wing", 12.0))

    class Boom:
        def validate(self, recipe: Recipe) -> None: ...
        def extract(self, ctx: object, recipe: Recipe) -> dict[str, float]:
            raise RuntimeError("kaboom")

    from csauto.qoi.registry import _REGISTRY

    monkeypatch.setitem(_REGISTRY, "boom", Boom)
    result = extract_case_qois(case, [Recipe(name="X", type="boom")])

    assert math.isnan(result.qois["X"])
    assert any("RuntimeError" in e and "kaboom" in e for e in result.errors)


# --- extract_runs_qois ------------------------------------------------------


def test_extract_runs_qois_walks_every_case(tmp_path: Path) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    _make_case(runs, "case0001", {"u": "30"}, ("wing", 12.0))
    _make_case(runs, "case0002", {"u": "35"}, ("wing", 6.0))
    _make_case(runs, "case0003", {"u": "40"})  # no CSV -> error row
    (runs / "MESH").mkdir()  # non-case dir, must be ignored

    results = extract_runs_qois(runs, [_force_recipe()])
    assert [r.case_id for r in results] == ["case0001", "case0002", "case0003"]
    assert results[0].qois["Cd"] == pytest.approx(24.0)
    assert results[1].qois["Cd"] == pytest.approx(12.0)
    assert math.isnan(results[2].qois["Cd"])
    assert results[2].status == "error"


def test_extract_runs_qois_filters_to_subset(tmp_path: Path) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    _make_case(runs, "case0001", {"u": "30"}, ("wing", 12.0))
    _make_case(runs, "case0002", {"u": "35"}, ("wing", 6.0))

    results = extract_runs_qois(runs, [_force_recipe()], cases=["case0002"])
    assert [r.case_id for r in results] == ["case0002"]


def test_extract_runs_qois_missing_runs_dir(tmp_path: Path) -> None:
    with pytest.raises(QoIError, match="runs_dir not found"):
        extract_runs_qois(tmp_path / "ghost", [_force_recipe()])


# --- Column ordering --------------------------------------------------------


def test_doe_column_order_drops_case_id_and_preserves_first_seen() -> None:
    results = [
        CaseResult(case_id="c1", doe_row={"case_id": "c1", "u": "30", "model": "k-omega"}),
        CaseResult(case_id="c2", doe_row={"case_id": "c2", "u": "35", "model": "k-eps", "aoa": "5"}),
    ]
    assert doe_column_order(results) == ["u", "model", "aoa"]


def test_qoi_column_order_aggregates_across_cases() -> None:
    results = [
        CaseResult(case_id="c1", qois={"Cd": 0.5}),
        CaseResult(case_id="c2", qois={"Cd": 0.6, "Cl": 0.7}),
    ]
    assert qoi_column_order(results) == ["Cd", "Cl"]


# --- write_table ------------------------------------------------------------


def _sample_results() -> list[CaseResult]:
    return [
        CaseResult(
            case_id="case0001",
            doe_row={"case_id": "case0001", "u": "30"},
            qois={"Cd": 0.42, "Cl": 0.51},
        ),
        CaseResult(
            case_id="case0002",
            doe_row={"case_id": "case0002", "u": "35"},
            qois={"Cd": math.nan, "Cl": 0.55},
            errors=["Cd: CSV not found"],
        ),
    ]


def test_write_table_csv(tmp_path: Path) -> None:
    out = tmp_path / "results.csv"
    write_table(_sample_results(), out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "case_id,u,Cd,Cl,_status,_errors"
    assert lines[1] == "case0001,30,0.42,0.51,ok,"
    assert lines[2] == "case0002,35,,0.55,error,Cd: CSV not found"


def test_write_table_tsv(tmp_path: Path) -> None:
    out = tmp_path / "results.tsv"
    write_table(_sample_results(), out)
    head = out.read_text(encoding="utf-8").splitlines()[0]
    assert head == "case_id\tu\tCd\tCl\t_status\t_errors"


def test_write_table_json(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    write_table(_sample_results(), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0] == {
        "case_id": "case0001",
        "u": "30",
        "Cd": 0.42,
        "Cl": 0.51,
        "_status": "ok",
        "_errors": "",
    }
    # NaN serialized as null in JSON
    assert data[1]["Cd"] is None
    assert data[1]["_status"] == "error"


def test_write_table_explicit_format_overrides_extension(tmp_path: Path) -> None:
    out = tmp_path / "results.dat"
    write_table(_sample_results(), out, format="csv")
    assert "case_id,u,Cd" in out.read_text(encoding="utf-8")


def test_write_table_unknown_format_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported output format"):
        write_table(_sample_results(), tmp_path / "results.xml")


def test_write_table_unknown_extension_without_format_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cannot infer output format"):
        write_table(_sample_results(), tmp_path / "results_no_ext")
