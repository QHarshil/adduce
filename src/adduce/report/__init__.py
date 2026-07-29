"""Report renderers keyed by output format name.

Third-party reporters register a callable ``CheckResult -> str`` under the
``adduce.reporters`` entry-point group; the entry-point name becomes the
``--format`` value.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points

from ..engine import CheckResult
from . import badge, json_report, latex, markdown, sarif

_BUILTIN_RENDERERS: dict[str, Callable[[CheckResult], str]] = {
    "json": json_report.render,
    "sarif": sarif.render,
    "markdown": markdown.render,
    "badge": badge.render,
    "latex": latex.render,
}

_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9_.:-]+")
_VALID_ENTRY_POINT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


class ReporterPluginWarning(UserWarning):
    """A configured reporter plugin could not be used safely."""


def _safe_label(value: object, fallback: str = "unknown") -> str:
    try:
        text = str(value)
    except Exception:
        return fallback
    text = _UNSAFE_LABEL.sub("?", text)[:80]
    return text or fallback


def _entry_point_field(entry_point: object, field: str) -> object:
    try:
        return getattr(entry_point, field)
    except Exception:
        return "unknown"


def _entry_point_label(entry_point: EntryPoint) -> str:
    name = _safe_label(_entry_point_field(entry_point, "name"), "unnamed")
    value = _safe_label(_entry_point_field(entry_point, "value"))
    return f"{name} ({value})"


def _warn_plugin(entry_point: EntryPoint, reason: str) -> None:
    warnings.warn(
        f"Skipped adduce.reporters plugin {_entry_point_label(entry_point)}: {reason}.",
        ReporterPluginWarning,
        stacklevel=2,
    )


def _warn_discovery() -> None:
    warnings.warn(
        "Could not discover adduce.reporters plugins; built-in reporters remain available.",
        ReporterPluginWarning,
        stacklevel=2,
    )


def _entry_point_key(entry_point: EntryPoint) -> tuple[str, str, str]:
    distribution = _entry_point_field(entry_point, "dist")
    distribution_name = _entry_point_field(distribution, "name")
    return (
        _safe_label(_entry_point_field(entry_point, "name")),
        _safe_label(_entry_point_field(entry_point, "value")),
        _safe_label(distribution_name),
    )


def _discover_renderers(
    entries: Iterable[EntryPoint],
) -> dict[str, Callable[[CheckResult], str]]:
    """Load reporters deterministically without allowing built-in shadowing."""
    renderers = dict(_BUILTIN_RENDERERS)
    try:
        ordered_entries = sorted(entries, key=_entry_point_key)
    except Exception:
        _warn_discovery()
        return renderers

    for entry_point in ordered_entries:
        try:
            name = entry_point.name
        except Exception:
            _warn_plugin(entry_point, "entry-point metadata is unreadable")
            continue
        if not isinstance(name, str) or _VALID_ENTRY_POINT_NAME.fullmatch(name) is None:
            _warn_plugin(entry_point, "entry-point name is invalid")
            continue
        if name in renderers:
            _warn_plugin(entry_point, "format name conflicts with an existing reporter")
            continue
        try:
            renderer = entry_point.load()
        except Exception:
            _warn_plugin(entry_point, "entry-point loading failed")
            continue
        if not callable(renderer):
            _warn_plugin(entry_point, "loaded object is not callable")
            continue
        renderers[name] = renderer
    return renderers


_REPORTER_ENTRY_POINTS: Iterable[EntryPoint]
try:
    _REPORTER_ENTRY_POINTS = entry_points(group="adduce.reporters")
except Exception:
    _warn_discovery()
    _REPORTER_ENTRY_POINTS = ()

RENDERERS = _discover_renderers(_REPORTER_ENTRY_POINTS)

__all__ = ["RENDERERS", "ReporterPluginWarning"]
