from __future__ import annotations


class QoIError(Exception):
    """Base class for all QoI subsystem errors."""


class RecipeError(QoIError):
    """Raised when a recipe definition is invalid (missing fields, bad values)."""


class UnknownRecipeTypeError(RecipeError):
    """Raised when a recipe references a type not registered in the registry."""


class SaturneVersionError(QoIError):
    """Raised when code_saturne is older than the minimum required for QoI extraction."""


__all__ = [
    "QoIError",
    "RecipeError",
    "SaturneVersionError",
    "UnknownRecipeTypeError",
]
