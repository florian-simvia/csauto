"""Patch setup.xml so the boundary fields a recipe needs are recorded.

Each extractor can declare ``required_setup_properties(recipe)`` returning a
list of property names (the ``name`` attribute of a ``<property>`` tag in
setup.xml). csauto walks every caseXXXX/DATA/setup.xml and toggles
``<postprocessing_recording status="off"/>`` to ``"on"`` for those names so the
fields appear at runtime — no manual click in the code_saturne GUI required.

Strategy: surgical regex on the XML text so original formatting and comments
are preserved. We never create missing ``<property>`` tags (that would be too
invasive without knowing the surrounding schema); instead, missing entries are
reported back to the caller, which surfaces them as warnings.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .recipe import Recipe
from .registry import get_extractor

# Inside one <property name="X" ...>...</property> block, toggle the
# postprocessing_recording status from off to on. The block-restricted lookahead
# (?!</property>) ensures we never bleed into a sibling property.
_PROPERTY_BLOCK_TEMPLATE = (
    r'(<property\s+name="{name}"[^>]*>(?:(?!</property>).)*?)'
    r'<postprocessing_recording\s+status="off"\s*/>'
)
_ENABLED_RE_TEMPLATE = (
    r'<property\s+name="{name}"[^>]*>(?:(?!</property>).)*?'
    r'<postprocessing_recording\s+status="on"\s*/>'
)
# Self-closing form: <property name="X" ... /> — enabled by default (no child).
_PRESENT_RE_TEMPLATE = r'<property\s+name="{name}"[^>]*?(?:/>|>)'


@dataclass(frozen=True)
class SetupPatchReport:
    """Outcome of patching one case's setup.xml."""

    case_dir: Path
    setup_path: Path | None
    enabled: list[str] = field(default_factory=list)
    already_on: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.enabled)


def collect_required_properties(recipes: Sequence[Recipe]) -> list[str]:
    """Return the deduplicated, ordered list of property names recipes require."""
    seen: dict[str, None] = {}
    for recipe in recipes:
        extractor_cls = get_extractor(recipe.type)
        extractor = extractor_cls()
        method = getattr(extractor, "required_setup_properties", None)
        if method is None:
            continue
        for prop in method(recipe):
            seen.setdefault(prop, None)
    return list(seen)


def patch_setup_in_case(case_dir: Path, properties: Sequence[str]) -> SetupPatchReport:
    """Enable post-processing recording for the given property names in one case.

    Returns a report describing what was changed, what was already enabled,
    and which property names were not found in the XML.
    """
    setup_path = _find_setup_file(case_dir)
    if setup_path is None:
        return SetupPatchReport(case_dir=case_dir, setup_path=None, missing=list(properties))

    text = setup_path.read_text(encoding="utf-8")
    new_text = text
    enabled: list[str] = []
    already_on: list[str] = []
    missing: list[str] = []

    for name in properties:
        new_text, status = _enable_property(new_text, name)
        if status == "enabled":
            enabled.append(name)
        elif status == "already_on":
            already_on.append(name)
        else:
            missing.append(name)

    if new_text != text:
        setup_path.write_text(new_text, encoding="utf-8")

    return SetupPatchReport(
        case_dir=case_dir,
        setup_path=setup_path,
        enabled=enabled,
        already_on=already_on,
        missing=missing,
    )


def patch_setup_for_recipes(runs_dir: Path, recipes: Sequence[Recipe]) -> list[SetupPatchReport]:
    """Patch every caseXXXX/DATA/setup.xml under runs_dir.

    Returns one report per case. The caller is expected to surface any
    ``missing`` entries as user-visible warnings.
    """
    if not recipes:
        return []
    properties = collect_required_properties(recipes)
    if not properties:
        return []
    reports: list[SetupPatchReport] = []
    for case_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("case")):
        reports.append(patch_setup_in_case(case_dir, properties))
    return reports


def _enable_property(text: str, name: str) -> tuple[str, str]:
    """Toggle ``status="off"`` to ``"on"`` for <property name=NAME>.

    Returns a tuple (new_text, status) where status is one of
    "enabled" (the property was off and is now on),
    "already_on" (the property exists and was already enabled),
    "missing" (no <property name=NAME> tag exists in the file).
    """
    block_pattern = re.compile(_PROPERTY_BLOCK_TEMPLATE.format(name=re.escape(name)), re.DOTALL)
    if block_pattern.search(text) is not None:
        new_text = block_pattern.sub(r'\1<postprocessing_recording status="on"/>', text, count=1)
        return new_text, "enabled"

    enabled_pattern = re.compile(_ENABLED_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL)
    if enabled_pattern.search(text):
        return text, "already_on"

    present_pattern = re.compile(_PRESENT_RE_TEMPLATE.format(name=re.escape(name)))
    if present_pattern.search(text):
        # Property exists but has no postprocessing_recording child — treat as
        # already enabled (the default in code_saturne when no <postprocessing_recording>
        # is present is to record).
        return text, "already_on"

    return text, "missing"


def _find_setup_file(case_dir: Path) -> Path | None:
    """Return caseXXXX/DATA/setup.xml if present, else None.

    csauto already has csauto.template.find_setup_file, but it raises on
    missing/ambiguous setups. For this patcher a missing setup is a soft
    failure (treated as "everything is missing") so we use a tolerant lookup.
    """
    candidate = case_dir / "DATA" / "setup.xml"
    if candidate.is_file():
        return candidate
    return None


__all__ = [
    "SetupPatchReport",
    "collect_required_properties",
    "patch_setup_for_recipes",
    "patch_setup_in_case",
]
