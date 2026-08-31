"""Text that came from a scanned repository, made safe to display.

A path, a branch name or a parsed detail string is repository-controlled. Written
straight to a terminal it can carry control sequences that clear the screen or
open an arbitrary hyperlink, and written into a Markdown report it carries them
to whoever opens the report next. JSON and SARIF escape them; prose formats do
not, so the escaping happens here instead.

This is the only implementation. A second one would drift, and the interesting
case is always the one the other copy forgot.
"""

from __future__ import annotations


def safe_display_text(value: str, limit: int | None = None) -> str:
    """Remove terminal controls and normalize whitespace in untrusted text."""
    printable = "".join(character if character.isprintable() else " " for character in value)
    cleaned = " ".join(printable.split())
    return cleaned[:limit] if limit is not None else cleaned
