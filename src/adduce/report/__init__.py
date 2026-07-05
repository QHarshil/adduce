"""Report renderers keyed by output format name.

Third-party reporters register a callable ``CheckResult -> str`` under the
``adduce.reporters`` entry-point group; the entry-point name becomes the
``--format`` value.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points

from ..engine import CheckResult
from . import badge, json_report, latex, markdown, sarif

RENDERERS: dict[str, Callable[[CheckResult], str]] = {
    "json": json_report.render,
    "sarif": sarif.render,
    "markdown": markdown.render,
    "badge": badge.render,
    "latex": latex.render,
}

for _ep in entry_points(group="adduce.reporters"):
    if _ep.name in RENDERERS:
        continue  # built-ins cannot be shadowed
    try:
        _renderer = _ep.load()
    except Exception:
        continue  # a broken plugin must not break reporting
    if callable(_renderer):
        RENDERERS[_ep.name] = _renderer

__all__ = ["RENDERERS"]
