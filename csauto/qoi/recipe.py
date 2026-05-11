from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import RecipeError

_RESERVED_KEYS = frozenset({"name", "type"})


@dataclass(frozen=True)
class Recipe:
    """A user-declared QoI to extract from each case.

    Attributes:
        name: User-facing column name in the result table (e.g. "Cd").
        type: Identifier of the extractor that will compute this QoI.
              Must match a key registered in csauto.qoi.registry.
        params: Type-specific parameters (boundary name, reference values,
                probe ids, etc.). Schema is the extractor's responsibility.
    """

    name: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


def parse_recipe(data: dict[str, Any], *, source: str = "[[qoi]]") -> Recipe:
    """Build a Recipe from a single TOML table.

    Validates only the common shape (name + type as non-empty strings).
    Type-specific validation is deferred to the extractor.

    Args:
        data: One [[qoi]] table from a parsed TOML document.
        source: A label used in error messages (e.g. file path + section index).
    """
    if not isinstance(data, dict):
        raise RecipeError(f"Invalid {source}: expected a table, got {type(data).__name__}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RecipeError(f"Invalid {source}: missing or empty 'name'")
    recipe_type = data.get("type")
    if not isinstance(recipe_type, str) or not recipe_type.strip():
        raise RecipeError(f"Invalid {source} '{name}': missing or empty 'type'")
    params = {k: v for k, v in data.items() if k not in _RESERVED_KEYS}
    return Recipe(name=name.strip(), type=recipe_type.strip(), params=params)


__all__ = ["Recipe", "parse_recipe"]
