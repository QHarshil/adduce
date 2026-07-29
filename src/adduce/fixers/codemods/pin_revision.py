"""libcst codemod: add ``revision="<sha>"`` to Hugging Face hub calls.

Applied only through ``adduce pin-remotes``, which shows the diff first
(``--diff``) and writes only with ``--write``. Pinning to the current SHA is
a forward guarantee, not recovery of the version historically used — the
command's output repeats this.
"""

from __future__ import annotations

import difflib

import libcst as cst

_MODEL_TERMINALS = frozenset({"from_pretrained", "SentenceTransformer"})
_DATASET_TERMINALS = frozenset({"load_dataset"})
_HUB_DOWNLOAD_TERMINALS = frozenset({"hf_hub_download", "snapshot_download"})
_PINNABLE_TERMINALS = _MODEL_TERMINALS | _DATASET_TERMINALS | _HUB_DOWNLOAD_TERMINALS


class _AddRevision(cst.CSTTransformer):
    def __init__(self, revisions: dict[tuple[str, str], str]) -> None:
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
        if not isinstance(identifier, str):
            return updated
        if terminal in _DATASET_TERMINALS:
            resolver_kind = "hf-dataset"
        elif terminal in _MODEL_TERMINALS:
            resolver_kind = "hf-model"
        else:
            repo_type = next(
                (
                    arg.value.evaluated_value
                    for arg in updated.args
                    if arg.keyword
                    and arg.keyword.value == "repo_type"
                    and isinstance(arg.value, cst.SimpleString)
                ),
                "model",
            )
            if repo_type not in {"model", "dataset"}:
                return updated
            resolver_kind = f"hf-{repo_type}"
        sha = self.revisions.get((resolver_kind, identifier))
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


def pin_revisions(
    source: str,
    revisions: dict[tuple[str, str], str],
) -> tuple[str, int]:
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
