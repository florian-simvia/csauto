from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .recipe import Recipe


@dataclass(frozen=True)
class Context:
    """Per-case context passed to an Extractor.

    The Context is intentionally minimal at the skeleton stage. Helper methods
    (read_listing, read_monitoring, probes, ...) will be added incrementally
    as concrete extractors require them.

    Attributes:
        case_dir: Path to caseXXXX/ in the RUNS directory.
        doe_row: Resolved DOE values for this case (column -> value).
        runs_dir: Path to the parent RUNS directory.
    """

    case_dir: Path
    doe_row: dict[str, Any] = field(default_factory=dict)
    runs_dir: Path | None = None


@runtime_checkable
class Extractor(Protocol):
    """Contract that every recipe-type implementation must satisfy.

    An Extractor knows:
      - how to validate a recipe's params (validate)
      - how to compute the QoI value(s) from a finished case (extract)

    Extractors that require code_saturne user-files to be injected at prepare
    time may additionally implement `cpp_template()` returning the path to a
    Jinja-like template; this will be picked up by the prepare pipeline in a
    later phase.
    """

    def validate(self, recipe: Recipe) -> None:
        """Raise RecipeError if the recipe's params are invalid for this type."""
        ...

    def extract(self, ctx: Context, recipe: Recipe) -> dict[str, float]:
        """Compute the QoI value(s) for one case.

        Returns a mapping {column_name: value}. Most extractors return a single
        entry keyed by recipe.name, but composite extractors (e.g. drag + lift
        from the same integration) may return several.
        """
        ...


__all__ = ["Context", "Extractor"]
