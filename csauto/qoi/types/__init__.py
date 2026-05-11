"""Built-in QoI recipe types.

Importing this package triggers registration of every shipped extractor via
the @register decorator. Add a new built-in type by creating a module here
and importing it below.
"""

from __future__ import annotations

from . import (
    force_coefficient,  # noqa: F401 -- side-effect: registers the extractor
    pressure_drop,  # noqa: F401 -- side-effect: registers the extractor
)

__all__: list[str] = []
