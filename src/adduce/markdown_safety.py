"""Safe rendering helpers for untrusted repository text in Markdown drafts."""

from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_ESCAPES = "\\`*_{}[]<>|"


def plain_inline(value: object) -> str:
    """Collapse untrusted text to one control-free line."""
    text = _CONTROL_RE.sub(" ", str(value))
    return " ".join(text.split())


def markdown_inline(value: object) -> str:
    """Render untrusted text without allowing Markdown structure or raw HTML."""
    text = plain_inline(value)
    for character in _INLINE_ESCAPES:
        text = text.replace(character, "\\" + character)
    return text


def markdown_code_span(value: object) -> str:
    """Render one untrusted line as a code span with a collision-free fence."""
    text = plain_inline(value)
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    fence = "`" * max(1, (max(runs) + 1) if runs else 1)
    padding = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def markdown_indented_lines(value: object) -> list[str]:
    """Contain untrusted multiline text in an indented literal block."""
    text = _CONTROL_RE.sub(" ", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return [f"    {line}" for line in (text.splitlines() or [""])]
