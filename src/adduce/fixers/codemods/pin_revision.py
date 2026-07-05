"""libcst codemod: add ``revision="<sha>"`` to Hugging Face hub calls.

Applied only through ``adduce pin-remotes``, which shows the diff first
(``--diff``) and writes only with ``--write``. Pinning to the current SHA is
a forward guarantee, not recovery of the version historically used — the
command's output repeats this.
"""

from __future__ import annotations

import difflib

import libcst as cst

_PINNABLE_TERMINALS = frozenset(
    {"from_pretrained", "load_dataset", "hf_hub_download", "snapshot_download", "SentenceTransformer"}
)


class _AddRevision(cst.CSTTransformer):
    def __init__(self, revisions: dict[str, str]) -> None:
        self.revisions = revisions
        self.changes = 0

    def _terminal_name(self, func: cst.BaseExpression) -> str | None:
        if isinstance(func, cst.Attribute):
            return func.attr.value
        if isinstance(func, cst.Name):
            return func.value
        return None

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.Call:
        terminal = self._terminal_name(updated.func)
        if terminal not in _PINNABLE_TERMINALS:
            return updated
        if any(arg.keyword and arg.keyword.value == "revision" for arg in updated.args):
            return updated
        if not updated.args:
            return updated
        first = updated.args[0].value
        if not isinstance(first, cst.SimpleString):
            return updated
        identifier = first.evaluated_value
        sha = self.revisions.get(identifier) if isinstance(identifier, str) else None
        if not sha:
            return updated
        revision_arg = cst.Arg(
            value=cst.SimpleString(f'"{sha}"'),
            keyword=cst.Name("revision"),
            equal=cst.AssignEqual(
                whitespace_before=cst.SimpleWhitespace(""),
                whitespace_after=cst.SimpleWhitespace(""),
            ),
        )
        self.changes += 1
        return updated.with_changes(args=[*updated.args, revision_arg])


def pin_revisions(source: str, revisions: dict[str, str]) -> tuple[str, int]:
    """Return (new_source, number_of_calls_pinned)."""
    module = cst.parse_module(source)
    transformer = _AddRevision(revisions)
    new_module = module.visit(transformer)
    return new_module.code, transformer.changes


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
