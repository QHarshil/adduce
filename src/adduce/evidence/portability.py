"""Portability evidence: things that only work on the author's machine.

Local absolute paths, hardcoded localhost endpoints, and committed secrets.
Secret detection records the location and kind only — the matched value is
never stored or echoed anywhere.

In a notebook, only code-cell source can hold an endpoint. Colab writes
``"base_uri": "https://localhost:8080/"`` into the metadata of every cell it
ran, and outputs echo whatever a server printed; neither is code a reviewer
would have to run. Cells are read in the nbformat 4 and 3 layouts that
``python_ast`` reads, and from the source strings of a notebook that does not
load.

:func:`secret_kinds` and :func:`secret_kind` are the one detector every other
layer uses. A second table would drift, and a security-relevant classifier that
disagrees with itself is worse than none.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..model import FileEntry, Repo

_SCANNABLE_SUFFIXES = frozenset(
    {
        ".py",
        ".sh",
        ".bash",
        ".yaml",
        ".yml",
        ".json",
        ".jsonl",
        ".cfg",
        ".ini",
        ".env",
        ".toml",
        ".md",
        ".rst",
        ".tex",
        ".txt",
        ".ipynb",
    }
)
_MAX_SCAN_BYTES = 2_000_000

_ABS_PATH_RE = re.compile(
    r"(/Users/\w[\w.-]*|/home/(?!runner\b|user\b)\w[\w.-]*|[A-Z]:\\Users\\\w+|~/(?:Desktop|Documents|Downloads)\b|/Volumes/\w+)"
)
_LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d{2,5}\b")
#: A JSON string or structural character. Applied only to text ``json.loads``
#: has already accepted. Numbers, booleans and null fall between matches and are
#: skipped: none of them is a line of source.
_JSON_TOKEN_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[{}\[\]:,]')
#: A JSON string literal, then the colon that makes it a key, if one follows.
_JSON_STRING_RE = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*")(\s*:)?')

#: Where a string sits in a JSON document: object keys and array indices.
_Path = tuple[object, ...]

#: (kind, pattern). Patterns match well-known token shapes, not generic entropy.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("W&B API key", re.compile(r"\bWANDB_API_KEY\s*[=:]\s*['\"]?[a-f0-9]{40}\b")),
)


def secret_kinds(text: str) -> tuple[str, ...]:
    """Every well-known secret shape ``text`` matches, in table order."""
    return tuple(kind for kind, pattern in _SECRET_PATTERNS if pattern.search(text))


def secret_kind(value: object) -> str | None:
    """Name the shape a single value matches, for a caller holding a value.

    Rendered before matching so a secret inside a short list is caught as
    readily as a bare string; no non-string scalar can match a pattern here.
    """
    kinds = secret_kinds(value if isinstance(value, str) else str(value))
    return kinds[0] if kinds else None


def _code_cell_strings(data: object) -> Iterator[tuple[_Path, str]]:
    """Each string of each code cell's source, with its path from the root.

    The layouts and keys are the ones ``python_ast._notebook_cells`` reads, so
    the two collectors see the same code.
    """
    if not isinstance(data, dict):
        return
    cells = data.get("cells")
    located: list[tuple[_Path, object]]
    if cells is None:
        # nbformat 3 kept its cells in worksheets and their code in ``input``.
        worksheets = data.get("worksheets")
        located = [
            (("worksheets", w, "cells", i), cell)
            for w, sheet in enumerate(worksheets if isinstance(worksheets, list) else [])
            if isinstance(sheet, dict) and isinstance(sheet.get("cells"), list)
            for i, cell in enumerate(sheet["cells"])
        ]
    elif isinstance(cells, list):
        located = [(("cells", i), cell) for i, cell in enumerate(cells)]
    else:
        return
    for path, cell in located:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        key = "source" if "source" in cell else "input"
        source = cell.get(key)
        if isinstance(source, str):
            yield (*path, key), source
        elif isinstance(source, list):
            for index, part in enumerate(source):
                if isinstance(part, str):
                    yield (*path, key, index), part


@dataclass
class _Frame:
    """One open JSON container during :func:`_offsets`'s walk."""

    is_object: bool
    #: The wanted paths through this container, keyed by their next step, or
    #: None when none passes through it.
    node: dict[object, Any] | None
    key: object = None
    index: int = 0
    expecting_key: bool = True

    def entry(self) -> object:
        """What the wanted paths hold at the current key or index."""
        if self.node is None:
            return None
        return self.node.get(self.key if self.is_object else self.index)


def _offsets(text: str, paths: Collection[_Path]) -> dict[_Path, int]:
    """The offset in ``text`` of the string at each of ``paths``.

    ``json.loads`` keeps the last of two equal keys, and the last string the
    walk finds at a path lies inside the value it kept, so a later find
    replaces an earlier one. A container no path passes through carries no
    node, so a deep output costs its length and no more.
    """
    tree: dict[object, Any] = {}
    for path in paths:
        node = tree
        for step in path[:-1]:
            node = node.setdefault(step, {})
        node[path[-1]] = path
    stack: list[_Frame] = []
    found: dict[_Path, int] = {}
    for match in _JSON_TOKEN_RE.finditer(text):
        token = match.group()
        head = token[0]
        if head in "{[":
            entry = stack[-1].entry() if stack else tree
            stack.append(_Frame(is_object=head == "{", node=entry if isinstance(entry, dict) else None))
            continue
        if head in "}]":
            stack.pop()
            continue
        top = stack[-1]
        if head == ":":
            top.expecting_key = False
        elif head == ",":
            top.expecting_key = True
            top.index += 1
        elif top.is_object and top.expecting_key:
            top.key = json.loads(token)
        elif isinstance(wanted := top.entry(), tuple):
            found[wanted] = match.start()
    return found


def _broken_notebook_endpoints(text: str) -> Iterator[tuple[int, str]]:
    """``(offset, endpoint)`` for each code source string of a notebook that does not load.

    A merge conflict or a truncated write breaks the JSON but leaves its string
    literals whole. Each value belongs to the key before it, so metadata and
    outputs are skipped without the nesting the damage broke. Writers that sort
    keys, nbformat among them, put a cell's type before its source, so a source
    counts when the last type named before it is ``code``.
    """
    key: object = None
    cell_type: object = None
    for match in _JSON_STRING_RE.finditer(text):
        literal, is_key = match.groups()
        try:
            value: str | None = json.loads(literal)
        except ValueError:
            # A literal the damage cut through: part of it could read as a
            # fragment of an endpoint, so it reads as nothing.
            value = None
        if is_key:
            key = value
            if key == "cell_type":
                cell_type = None
        elif key == "cell_type":
            cell_type = value
        elif (
            key in ("source", "input")
            and cell_type == "code"
            and value is not None
            and (localhost := _LOCALHOST_RE.search(value))
        ):
            yield match.start(), localhost.group(0)


def _with_lines(text: str, found: Iterable[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    """Turn ascending ``(offset, value)`` pairs into ``(line, value)``."""
    line, position = 1, 0
    for offset, value in found:
        line += text.count("\n", position, offset)
        position = offset
        yield line, value


def _notebook_endpoints(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, endpoint)`` for each code-cell source string naming one.

    ``line`` counts ``\\n`` in the raw file, as :meth:`PortabilityConsumer.feed`
    does for every other file, so a reported location is a line of the
    ``.ipynb`` a reader can open.
    """
    try:
        data = json.loads(text)
    except (ValueError, RecursionError, MemoryError):
        yield from _with_lines(text, _broken_notebook_endpoints(text))
        return
    named = {
        path: localhost.group(0)
        for path, source in _code_cell_strings(data)
        if (localhost := _LOCALHOST_RE.search(source))
    }
    # Most notebooks name no endpoint in code, and they should not pay for a
    # walk over every token to prove it.
    if not named:
        return
    offsets = _offsets(text, named)
    # Arrays keep their order through json.loads, so the paths come out in the
    # order their strings appear in the file.
    yield from _with_lines(text, ((offsets[path], endpoint) for path, endpoint in named.items()))


@dataclass(frozen=True)
class PortabilityHit:
    kind: str    # abs_path | localhost | secret
    detail: str  # for secrets: the kind label only, never the value
    file: str
    line: int


@dataclass
class PortabilityEvidence:
    hits: list[PortabilityHit] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[PortabilityHit]:
        return [h for h in self.hits if h.kind == kind]


class PortabilityConsumer:
    """The per-file half of this collector, for the shared read pass.

    Holds no state beyond the evidence it is accumulating, so running it inside
    the shared pass and running it over its own walk produce the same result.
    """

    def __init__(self) -> None:
        self.evidence = PortabilityEvidence()

    def wants(self, entry: FileEntry) -> bool:
        return (
            entry.suffix in _SCANNABLE_SUFFIXES
            and entry.size <= _MAX_SCAN_BYTES
            and not str(entry.path).startswith(".adduce/cache/")
        )

    def feed(self, entry: FileEntry, text: str) -> None:
        rel = str(entry.path)
        hits = self.evidence.hits
        notebook = entry.suffix == ".ipynb"
        # split("\n"), not splitlines(): splitlines() also breaks on \f, \v,
        # \x1c-\x1e, \x85, U+2028 and U+2029, none of which the CPython tokenizer
        # treats as a line break. A file containing one would number these lines
        # differently from the lines a finding is anchored to, which moves a
        # suppression pragma onto some other finding and can put a reported line
        # past the end of the file.
        for lineno, line in enumerate(text.split("\n"), start=1):
            for match in _ABS_PATH_RE.finditer(line):
                hits.append(PortabilityHit("abs_path", match.group(0), rel, lineno))
            if not notebook and (localhost := _LOCALHOST_RE.search(line)):
                hits.append(PortabilityHit("localhost", localhost.group(0), rel, lineno))
            for kind in secret_kinds(line):
                hits.append(PortabilityHit("secret", kind, rel, lineno))
        if notebook:
            reported = 0
            for lineno, endpoint in _notebook_endpoints(text):
                if lineno != reported:
                    hits.append(PortabilityHit("localhost", endpoint, rel, lineno))
                    reported = lineno


def collect_portability(repo: Repo) -> PortabilityEvidence:
    """Walk and scan in one call, for a caller outside the shared pass."""
    consumer = PortabilityConsumer()
    for entry in repo.files:
        if not consumer.wants(entry):
            continue
        text = repo.read_text(entry.path)
        if text is None:
            continue
        consumer.feed(entry, text)
    return consumer.evidence
