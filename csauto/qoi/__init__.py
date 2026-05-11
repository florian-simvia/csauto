"""QoI auto-postprocessing — recipe-driven extraction of quantities of interest.

This package is the skeleton for the auto-postprocessing feature. It defines
the contracts (Recipe, Extractor, Context) and the registry that future
extractor implementations plug into. No extractor is shipped yet.

Note: the feature requires code_saturne >= 9. csauto itself remains
compatible with older versions; only the QoI feature is gated.
"""

from __future__ import annotations

from .errors import QoIError, RecipeError, SaturneVersionError, UnknownRecipeTypeError
from .extractor import Context, Extractor
from .recipe import Recipe, parse_recipe
from .registry import available_types, get_extractor, register
from .version import MIN_SATURNE_VERSION, detect_saturne_version, is_compatible

__all__ = [
    "MIN_SATURNE_VERSION",
    "Context",
    "Extractor",
    "QoIError",
    "Recipe",
    "RecipeError",
    "SaturneVersionError",
    "UnknownRecipeTypeError",
    "available_types",
    "detect_saturne_version",
    "get_extractor",
    "is_compatible",
    "parse_recipe",
    "register",
]
