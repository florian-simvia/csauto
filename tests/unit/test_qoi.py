from __future__ import annotations

from collections.abc import Iterator

import pytest

from csauto.qoi import (
    MIN_SATURNE_VERSION,
    Context,
    Extractor,
    Recipe,
    UnknownRecipeTypeError,
    available_types,
    detect_saturne_version,
    get_extractor,
    is_compatible,
    parse_recipe,
    register,
)
from csauto.qoi.errors import RecipeError
from csauto.qoi.registry import _REGISTRY
from csauto.qoi.version import format_version

# --- Recipe parsing ---------------------------------------------------------


def test_parse_recipe_minimal_ok() -> None:
    recipe = parse_recipe({"name": "Cd", "type": "force_coefficient"})
    assert recipe.name == "Cd"
    assert recipe.type == "force_coefficient"
    assert recipe.params == {}


def test_parse_recipe_keeps_extra_keys_in_params() -> None:
    recipe = parse_recipe({"name": "Cd", "type": "force_coefficient", "boundary": "wing", "ref_area": 1.5})
    assert recipe.params == {"boundary": "wing", "ref_area": 1.5}


def test_parse_recipe_strips_whitespace_in_name_and_type() -> None:
    recipe = parse_recipe({"name": "  Cd ", "type": "  force_coefficient "})
    assert recipe.name == "Cd"
    assert recipe.type == "force_coefficient"


def test_parse_recipe_rejects_missing_name() -> None:
    with pytest.raises(RecipeError, match="missing or empty 'name'"):
        parse_recipe({"type": "force_coefficient"})


def test_parse_recipe_rejects_blank_name() -> None:
    with pytest.raises(RecipeError, match="missing or empty 'name'"):
        parse_recipe({"name": "  ", "type": "force_coefficient"})


def test_parse_recipe_rejects_missing_type() -> None:
    with pytest.raises(RecipeError, match="missing or empty 'type'"):
        parse_recipe({"name": "Cd"})


def test_parse_recipe_rejects_non_dict() -> None:
    with pytest.raises(RecipeError, match="expected a table"):
        parse_recipe("not a dict")  # type: ignore[arg-type]


# --- Registry ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry() -> Iterator[None]:
    """Each test gets a fresh registry, with the original content restored after."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


class _FakeExtractor:
    def validate(self, recipe: Recipe) -> None:  # pragma: no cover - protocol stub
        pass

    def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]:  # pragma: no cover
        return {recipe.name: 0.0}


def test_register_and_lookup() -> None:
    register("dummy")(_FakeExtractor)
    assert "dummy" in available_types()
    assert get_extractor("dummy") is _FakeExtractor


def test_register_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        register("   ")


def test_register_rejects_duplicate_name() -> None:
    register("dummy")(_FakeExtractor)

    class _Other:
        def validate(self, recipe: Recipe) -> None: ...
        def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]:
            return {}

    with pytest.raises(ValueError, match="already registered"):
        register("dummy")(_Other)  # type: ignore[arg-type]


def test_register_idempotent_for_same_class() -> None:
    register("dummy")(_FakeExtractor)
    # registering the same class again under the same name is a no-op
    register("dummy")(_FakeExtractor)
    assert get_extractor("dummy") is _FakeExtractor


def test_get_extractor_unknown_raises() -> None:
    with pytest.raises(UnknownRecipeTypeError, match="Unknown recipe type"):
        get_extractor("ghost")


# --- Extractor protocol -----------------------------------------------------


def test_fake_extractor_satisfies_protocol() -> None:
    assert isinstance(_FakeExtractor(), Extractor)


# --- Version detection ------------------------------------------------------


def test_min_saturne_version_is_9() -> None:
    assert MIN_SATURNE_VERSION[0] >= 9


def test_is_compatible_with_none_returns_false() -> None:
    assert is_compatible(None) is False


def test_is_compatible_with_old_version_returns_false() -> None:
    assert is_compatible((8, 3)) is False


def test_is_compatible_with_min_or_newer_returns_true() -> None:
    assert is_compatible((9, 0)) is True
    assert is_compatible((10, 1, 2)) is True


def test_format_version_unknown() -> None:
    assert format_version(None) == "unknown"


def test_format_version_tuple() -> None:
    assert format_version((9, 0, 1)) == "9.0.1"


def test_detect_saturne_version_returns_none_when_no_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("csauto.qoi.version.shutil.which", lambda _name: None)
    assert detect_saturne_version(saturne_bin=None) is None


def test_detect_saturne_version_parses_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        stdout = "code_saturne 9.0.1 (build 2026-01-15)"
        stderr = ""

    monkeypatch.setattr("csauto.qoi.version.shutil.which", lambda _name: "/usr/bin/code_saturne")
    monkeypatch.setattr("csauto.qoi.version.subprocess.run", lambda *a, **kw: _Result())

    assert detect_saturne_version() == (9, 0, 1)


def test_detect_saturne_version_handles_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_kw: object) -> object:
        raise OSError("boom")

    monkeypatch.setattr("csauto.qoi.version.shutil.which", lambda _name: "/usr/bin/code_saturne")
    monkeypatch.setattr("csauto.qoi.version.subprocess.run", _boom)

    assert detect_saturne_version() is None
