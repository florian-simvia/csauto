from __future__ import annotations

from pathlib import Path

import pytest

from csauto.qoi import Recipe
from csauto.qoi.setup_patcher import (
    SetupPatchReport,
    collect_required_properties,
    patch_setup_for_recipes,
    patch_setup_in_case,
)

# --- Test helpers -----------------------------------------------------------


def _make_case_with_setup(tmp_path: Path, setup_xml: str, case_name: str = "case0001") -> Path:
    case_dir = tmp_path / case_name
    (case_dir / "DATA").mkdir(parents=True)
    (case_dir / "DATA" / "setup.xml").write_text(setup_xml, encoding="utf-8")
    return case_dir


def _force_recipe(boundary: str = "wing") -> Recipe:
    return Recipe(
        name="Cd",
        type="force_coefficient",
        params={
            "boundary": boundary,
            "direction": [1.0, 0.0, 0.0],
            "ref_area": 1.0,
            "ref_velocity": 1.0,
            "ref_density": 1.0,
        },
    )


_SETUP_TEMPLATE_FULL_OFF = """\
<root>
  <analysis_control>
    <output>
      <property name="stress" label="Stress" support="boundary">
        <postprocessing_recording status="off"/>
      </property>
      <property name="stress_normal" label="Stress, normal" support="boundary">
        <postprocessing_recording status="off"/>
      </property>
      <property name="stress_tangential" label="Stress, tangential" support="boundary">
        <postprocessing_recording status="off"/>
      </property>
    </output>
  </analysis_control>
</root>
"""

_SETUP_TEMPLATE_MIXED = """\
<root>
  <output>
    <property name="stress" label="Stress" support="boundary">
      <postprocessing_recording status="off"/>
    </property>
    <property name="stress_normal" label="Stress, normal" support="boundary">
      <postprocessing_recording status="on"/>
    </property>
    <property name="stress_tangential" label="Stress, tangential" support="boundary"/>
  </output>
</root>
"""

_SETUP_TEMPLATE_NO_STRESS = """\
<root>
  <output>
    <property name="yplus" label="Yplus" support="boundary"/>
  </output>
</root>
"""


# --- collect_required_properties --------------------------------------------


def test_collect_required_properties_for_force_coefficient() -> None:
    props = collect_required_properties([_force_recipe()])
    assert props == ["stress", "stress_normal", "stress_tangential"]


def test_collect_required_properties_dedupes_across_recipes() -> None:
    # Two force_coefficient recipes ask for the same 3 fields — collected once.
    recipes = [_force_recipe("wing"), _force_recipe("fuselage")]
    props = collect_required_properties(recipes)
    assert props == ["stress", "stress_normal", "stress_tangential"]


def test_collect_required_properties_skips_extractors_without_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extractors that don't declare required_setup_properties contribute nothing."""

    class Plain:
        def validate(self, recipe: Recipe) -> None: ...
        def extract(self, ctx: object, recipe: Recipe) -> dict[str, float]:
            return {}

    from csauto.qoi.registry import _REGISTRY

    monkeypatch.setitem(_REGISTRY, "plain", Plain)
    assert collect_required_properties([Recipe(name="X", type="plain")]) == []


# --- patch_setup_in_case ----------------------------------------------------


def test_patch_setup_enables_all_off_properties(tmp_path: Path) -> None:
    case = _make_case_with_setup(tmp_path, _SETUP_TEMPLATE_FULL_OFF)
    report = patch_setup_in_case(case, ["stress", "stress_normal", "stress_tangential"])
    assert report.enabled == ["stress", "stress_normal", "stress_tangential"]
    assert report.already_on == []
    assert report.missing == []
    assert report.changed is True

    text = (case / "DATA" / "setup.xml").read_text(encoding="utf-8")
    assert 'status="off"' not in text
    assert text.count('status="on"') == 3


def test_patch_setup_mixed_state(tmp_path: Path) -> None:
    """One off, one on, one self-closing — only the off must be toggled."""
    case = _make_case_with_setup(tmp_path, _SETUP_TEMPLATE_MIXED)
    report = patch_setup_in_case(case, ["stress", "stress_normal", "stress_tangential"])
    assert report.enabled == ["stress"]
    assert set(report.already_on) == {"stress_normal", "stress_tangential"}
    assert report.missing == []

    text = (case / "DATA" / "setup.xml").read_text(encoding="utf-8")
    assert 'status="off"' not in text


def test_patch_setup_reports_missing_property(tmp_path: Path) -> None:
    case = _make_case_with_setup(tmp_path, _SETUP_TEMPLATE_NO_STRESS)
    report = patch_setup_in_case(case, ["stress", "stress_normal", "stress_tangential"])
    assert report.enabled == []
    assert report.already_on == []
    assert report.missing == ["stress", "stress_normal", "stress_tangential"]
    # Setup must be untouched when nothing matched.
    assert (case / "DATA" / "setup.xml").read_text(encoding="utf-8") == _SETUP_TEMPLATE_NO_STRESS


def test_patch_setup_missing_setup_xml(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()
    report = patch_setup_in_case(case_dir, ["stress"])
    assert report.setup_path is None
    assert report.missing == ["stress"]
    assert report.changed is False


def test_patch_setup_is_idempotent(tmp_path: Path) -> None:
    """Running the patcher twice yields the same XML the second time."""
    case = _make_case_with_setup(tmp_path, _SETUP_TEMPLATE_FULL_OFF)
    props = ["stress", "stress_normal", "stress_tangential"]
    patch_setup_in_case(case, props)
    after_first = (case / "DATA" / "setup.xml").read_text(encoding="utf-8")
    report = patch_setup_in_case(case, props)
    assert report.enabled == []
    assert set(report.already_on) == set(props)
    assert (case / "DATA" / "setup.xml").read_text(encoding="utf-8") == after_first


def test_patch_setup_preserves_surrounding_xml(tmp_path: Path) -> None:
    """We must only touch the postprocessing_recording attribute, nothing else."""
    setup_with_neighbours = """\
<root>
  <!-- some comment to preserve -->
  <property name="density" choice="constant" label="Density">
    <initial_value>1.225</initial_value>
  </property>
  <property name="stress" label="Stress" support="boundary">
    <postprocessing_recording status="off"/>
  </property>
  <property name="yplus" label="Yplus" support="boundary"/>
</root>
"""
    case = _make_case_with_setup(tmp_path, setup_with_neighbours)
    patch_setup_in_case(case, ["stress"])
    text = (case / "DATA" / "setup.xml").read_text(encoding="utf-8")
    assert "some comment to preserve" in text
    assert "<initial_value>1.225</initial_value>" in text
    assert '<property name="yplus" label="Yplus" support="boundary"/>' in text


# --- patch_setup_for_recipes ------------------------------------------------


def test_patch_setup_for_recipes_walks_every_case(tmp_path: Path) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    for name in ("case0001", "case0002", "case0003"):
        case = runs / name
        (case / "DATA").mkdir(parents=True)
        (case / "DATA" / "setup.xml").write_text(_SETUP_TEMPLATE_FULL_OFF, encoding="utf-8")
    # Non-case directory must be ignored
    (runs / "MESH").mkdir()

    reports = patch_setup_for_recipes(runs, [_force_recipe()])
    assert {r.case_dir.name for r in reports} == {"case0001", "case0002", "case0003"}
    assert all(r.enabled == ["stress", "stress_normal", "stress_tangential"] for r in reports)


def test_patch_setup_for_recipes_empty_recipes_is_noop(tmp_path: Path) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    (runs / "case0001").mkdir()
    assert patch_setup_for_recipes(runs, []) == []


def test_patch_setup_for_recipes_skips_when_no_property_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recipes whose extractors don't declare required_setup_properties → no scan."""

    class Plain:
        def validate(self, recipe: Recipe) -> None: ...
        def extract(self, ctx: object, recipe: Recipe) -> dict[str, float]:
            return {}

    from csauto.qoi.registry import _REGISTRY

    monkeypatch.setitem(_REGISTRY, "plain", Plain)
    runs = tmp_path / "RUNS"
    runs.mkdir()
    (runs / "case0001").mkdir()
    assert patch_setup_for_recipes(runs, [Recipe(name="X", type="plain")]) == []


# --- SetupPatchReport -------------------------------------------------------


def test_setup_patch_report_changed_flag() -> None:
    r1 = SetupPatchReport(case_dir=Path("/x"), setup_path=Path("/x/DATA/setup.xml"))
    assert r1.changed is False

    r2 = SetupPatchReport(
        case_dir=Path("/x"),
        setup_path=Path("/x/DATA/setup.xml"),
        enabled=["stress"],
    )
    assert r2.changed is True
