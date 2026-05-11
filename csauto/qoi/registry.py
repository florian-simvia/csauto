from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import UnknownRecipeTypeError

if TYPE_CHECKING:
    from collections.abc import Callable

    from .extractor import Extractor

_REGISTRY: dict[str, type[Extractor]] = {}


def register(type_name: str) -> Callable[[type[Extractor]], type[Extractor]]:
    """Class decorator that registers an Extractor under a recipe type name.

    Example:
        @register("force_coefficient")
        class ForceCoefficientExtractor:
            ...
    """
    key = type_name.strip()
    if not key:
        raise ValueError("Recipe type name must be a non-empty string")

    def _decorator(cls: type[Extractor]) -> type[Extractor]:
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not cls:
            raise ValueError(f"Recipe type {key!r} already registered to {existing.__name__}")
        _REGISTRY[key] = cls
        return cls

    return _decorator


def get_extractor(type_name: str) -> type[Extractor]:
    """Return the Extractor class registered under the given recipe type."""
    try:
        return _REGISTRY[type_name]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise UnknownRecipeTypeError(f"Unknown recipe type {type_name!r}. Registered types: {known}") from exc


def available_types() -> list[str]:
    """Return the list of currently registered recipe types, sorted."""
    return sorted(_REGISTRY)


__all__ = ["available_types", "get_extractor", "register"]
