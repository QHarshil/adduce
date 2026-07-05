"""README reproducibility badge: shields.io endpoint JSON or self-contained SVG.

Publish the emitted JSON anywhere reachable (e.g. a gist or gh-pages) and
embed ``https://img.shields.io/endpoint?url=<location of the JSON>``, or
commit the SVG directly — no hosted endpoint either way.
"""

from __future__ import annotations

import json

from ..engine import CheckResult

_SVG_COLORS = {
    "brightgreen": "#4c1",
    "green": "#97ca00",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
}


def _color(total: float) -> str:
    if total >= 85:
        return "brightgreen"
    if total >= 70:
        return "green"
    if total >= 50:
        return "yellow"
    return "orange"


def render(result: CheckResult) -> str:
    total = result.card.total
    return json.dumps(
        {
            "schemaVersion": 1,
            "label": "reproducibility",
            "message": f"{total:.0f}/100",
            "color": _color(total),
        },
        indent=2,
    )


def render_svg(result: CheckResult) -> str:
    """A self-contained flat badge SVG the GitHub Action can commit in-repo."""
    total = result.card.total
    label = "reproducibility"
    message = f"{total:.0f}/100"
    color = _SVG_COLORS[_color(total)]
    label_width = 6 * len(label) + 10
    message_width = 6 * len(message) + 10
    width = label_width + message_width
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{label}: {message}">
  <title>{label}: {message}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/>
    <rect width="{width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_width / 2}" y="14">{label}</text>
    <text x="{label_width + message_width / 2}" y="14">{message}</text>
  </g>
</svg>"""
