"""The callables the ``adduce.reporters`` entry points resolve to."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adduce.engine import CheckResult


def render(result: CheckResult) -> str:
    card = result.card
    return f"contract-plugin profile={card.profile_name} findings={len(card.findings)}\n"


def render_shadow(result: CheckResult) -> str:
    """Registered under the built-in name ``json``, which must reject it.

    A test asserts the built-in renderer still owns that format name, so this
    body is unreachable and its output is a marker for the failure mode.
    """
    return "shadowed-builtin-format\n"
