"""The stable import location for adduce's plugin surface.

Every name here is covered by the change policy in ``docs/plugin-api.md`` and
is re-exported unchanged from the module that defines it. The module holds no
logic, so the surface a contract test imports is the surface that is promised.
The older import paths keep working.
"""

from __future__ import annotations

from . import __version__
from .evidence import Evidence, collect
from .rules import (
    BUILTIN_RULES,
    Category,
    Finding,
    FindingItem,
    JsonValue,
    Location,
    Rule,
    Status,
    discover_rules,
    summarize_items,
)

# isort: split
# Imported last. Importing adduce.report runs reporter discovery, and a
# reporter plugin that imports this module during discovery must find every
# name above already bound.
from .report import RENDERERS, ReporterPluginWarning

__all__ = [
    "BUILTIN_RULES",
    "RENDERERS",
    "Category",
    "Evidence",
    "Finding",
    "FindingItem",
    "JsonValue",
    "Location",
    "ReporterPluginWarning",
    "Rule",
    "Status",
    "__version__",
    "collect",
    "discover_rules",
    "summarize_items",
]
