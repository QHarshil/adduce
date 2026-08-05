#!/usr/bin/env python3
"""Fail closed unless every local Markdown link under a root resolves.

Scans the authored Markdown of the project: the root README, CONTRIBUTING,
SECURITY and CHANGELOG files, plus every ``.md`` file under ``docs/`` and
``corpus/``. Any of those that is absent under the root is skipped, because an
extracted source distribution ships a subset. The corpus working directories
(clones, outputs, labels, reports, snapshots, derived) hold third-party
repositories and generated run artifacts rather than authored documentation and
are never scanned.

Only local targets are resolved, relative to the directory of the file that
contains them. A target carrying a URI scheme is external: it is checked for
basic syntax and never fetched, so this script performs no network access. A
target that resolves to a directory is accepted when that directory exists; a
directory is resolved as a plain filesystem path with no index-file lookup. A
target that resolves outside the root is rejected even when the path exists,
because an extracted distribution has no such parent to resolve against.

Fragments are validated against GitHub-style heading slugs and against explicit
``name``/``id`` attributes on any HTML tag in the target document.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

_ROOT_DOCUMENTS = ("CHANGELOG.md", "CONTRIBUTING.md", "README.md", "SECURITY.md")
_DOCUMENT_TREES = ("corpus", "docs")
_UNSCANNED_TREES = (
    "corpus/clones",
    "corpus/derived",
    "corpus/labels",
    "corpus/outputs",
    "corpus/reports",
    "corpus/snapshots",
)

_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(?P<text>.*?))?[ \t]*$")
_CLOSING_HASHES_RE = re.compile(r"(?:^|[ \t])#+$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_HTML_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")
_HTML_ANCHOR_RE = re.compile(r"""\b(?:name|id)[ \t]*=[ \t]*(?:"([^"]*)"|'([^']*)')""")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_UNSLUGGABLE_RE = re.compile(r"[^\w\s-]", re.UNICODE)

_TEXT = r"(?:[^\[\]\\]|\\.|\[[^\[\]]*\])*"
_TITLE = r"""(?:[ \t]+(?:"[^"]*"|'[^']*'|\([^()]*\)))?"""
_TARGET = r"""(?:<(?P<angle>[^<>]*)>|(?P<plain>(?:[^\s()\\]|\\.|\([^\s()]*\))*))"""
_LINK_RE = re.compile(
    rf"(?P<image>!?)\[(?P<text>{_TEXT})\]"
    rf"(?:\([ \t]*{_TARGET}{_TITLE}[ \t]*\)|\[(?P<label>[^\[\]]*)\])"
)
_DEFINITION_RE = re.compile(
    rf"^ {{0,3}}\[(?P<label>[^\[\]]+)\]:[ \t]*{_TARGET}{_TITLE}[ \t]*$"
)
_LINK_IN_HEADING_RE = re.compile(rf"\[(?P<text>{_TEXT})\]\([^()]*\)")


class MarkdownScanError(ValueError):
    """The Markdown tree cannot be read as requested."""


@dataclass(frozen=True)
class _Link:
    """A link target, or a reference label when ``reference`` is set."""

    line: int
    value: str
    image: bool
    reference: bool


@dataclass(frozen=True)
class _Document:
    path: Path
    relative: str
    anchors: frozenset[str]
    definitions: frozenset[str]
    links: tuple[_Link, ...]


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MarkdownScanError(f"cannot read {path}: {exc}") from exc


def _documents(root: Path) -> list[Path]:
    found: list[Path] = []
    for name in _ROOT_DOCUMENTS:
        candidate = root / name
        if candidate.is_file():
            found.append(candidate)
    for tree in _DOCUMENT_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            relative = path.relative_to(root).as_posix()
            if any(relative.startswith(f"{skip}/") for skip in _UNSCANNED_TREES):
                continue
            if path.is_file():
                found.append(path)
    return sorted(found)


def _lines_outside_fences(lines: list[str]) -> list[tuple[int, str]]:
    outside: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for number, line in enumerate(lines, start=1):
        match = _FENCE_RE.match(line)
        if match is not None:
            marker = match.group("fence")
            info = match.group("info")
            if fence is not None:
                if marker[0] == fence[0] and len(marker) >= fence[1] and not info.strip():
                    fence = None
                continue
            if marker[0] != "`" or "`" not in info:
                fence = (marker[0], len(marker))
                continue
        if fence is None:
            outside.append((number, line))
    return outside


def _mask_code_spans(line: str) -> str:
    runs = list(_BACKTICK_RUN_RE.finditer(line))
    masked = list(line)
    index = 0
    while index < len(runs):
        opening = runs[index]
        closing = next(
            (
                candidate
                for candidate in range(index + 1, len(runs))
                if runs[candidate].group() == opening.group()
            ),
            None,
        )
        if closing is None:
            break
        for position in range(opening.start(), runs[closing].end()):
            masked[position] = " "
        index = closing + 1
    return "".join(masked)


def _normalise_label(label: str) -> str:
    return " ".join(label.split()).lower()


def _slug(heading: str) -> str:
    plain = _LINK_IN_HEADING_RE.sub(r"\g<text>", heading)
    return re.sub(r"\s", "-", _UNSLUGGABLE_RE.sub("", plain).lower())


def _anchors(lines: list[str]) -> frozenset[str]:
    anchors: set[str] = set()
    occurrences: dict[str, int] = {}
    for _, line in _lines_outside_fences(lines):
        heading = _HEADING_RE.match(line)
        text = heading.group("text") if heading is not None else None
        if text:
            slug = _slug(_CLOSING_HASHES_RE.sub("", text).strip())
            if slug:
                # GitHub disambiguates repeated headings with a deterministic
                # occurrence suffix, so each repeat owns a distinct anchor.
                seen = occurrences.get(slug, 0)
                occurrences[slug] = seen + 1
                anchors.add(slug if seen == 0 else f"{slug}-{seen}")
        for tag in _HTML_TAG_RE.findall(line):
            for double, single in _HTML_ANCHOR_RE.findall(tag):
                anchors.add(double or single)
    return frozenset(anchors)


def _inline_target(match: re.Match[str]) -> str:
    angle = match.group("angle")
    return angle if angle is not None else match.group("plain")


def _collect_links(text: str, number: int) -> list[_Link]:
    links: list[_Link] = []
    for match in _LINK_RE.finditer(text):
        image = match.group("image") == "!"
        label = match.group("label")
        if label is None:
            links.append(_Link(number, _inline_target(match), image, False))
        else:
            links.append(
                _Link(number, _normalise_label(label or match.group("text")), image, True)
            )
        inner = match.group("text")
        if "[" in inner:
            links.extend(_collect_links(inner, number))
    return links


def _parse(path: Path, root: Path) -> _Document:
    lines = _read_lines(path)
    definitions: set[str] = set()
    links: list[_Link] = []
    for number, line in _lines_outside_fences(lines):
        masked = _mask_code_spans(line)
        definition = _DEFINITION_RE.match(masked)
        if definition is not None:
            definitions.add(_normalise_label(definition.group("label")))
            links.append(_Link(number, _inline_target(definition), False, False))
            continue
        links.extend(_collect_links(masked, number))
    return _Document(
        path=path,
        relative=path.relative_to(root).as_posix(),
        anchors=_anchors(lines),
        definitions=frozenset(definitions),
        links=tuple(links),
    )


def _external_problem(target: str) -> str | None:
    parts = urlsplit(target)
    if parts.scheme in {"http", "https"} and not parts.netloc:
        return f"external link has no host: {target}"
    if parts.scheme == "mailto" and "@" not in parts.path:
        return f"mailto link has no address: {target}"
    return None


def _target_anchors(path: Path, cache: dict[Path, frozenset[str]]) -> frozenset[str]:
    known = cache.get(path)
    if known is None:
        known = _anchors(_read_lines(path))
        cache[path] = known
    return known


def _link_problem(
    document: _Document,
    link: _Link,
    root: Path,
    cache: dict[Path, frozenset[str]],
) -> str | None:
    raw = link.value.strip()
    if not raw:
        return "link target is empty"
    if raw.startswith("//") or _SCHEME_RE.match(raw):
        return _external_problem(raw)
    location, _, fragment = raw.partition("#")
    if location:
        resolved = (document.path.parent / unquote(location)).resolve()
        # A traversal target is rejected on shape, not on existence: the parent
        # it reaches for is absent from an extracted distribution.
        if not resolved.is_relative_to(root):
            return f"link target resolves outside the root: {raw}"
        if not resolved.exists():
            return f"link target does not exist: {raw}"
    else:
        resolved = document.path
    if not fragment:
        return None
    if resolved.is_dir() or resolved.suffix.lower() != ".md":
        return None
    anchors = document.anchors if resolved == document.path else _target_anchors(resolved, cache)
    if unquote(fragment) not in anchors:
        where = resolved.relative_to(root).as_posix()
        return f"anchor not found in {where}: #{fragment}"
    return None


def check_markdown_links(root: Path, check_images: bool = False) -> list[str]:
    """Return one diagnostic per broken local link, in document and line order."""
    if not root.is_dir():
        raise MarkdownScanError(f"root is not a directory: {root}")
    resolved_root = root.resolve()
    cache: dict[Path, frozenset[str]] = {}
    problems: list[str] = []
    for path in _documents(resolved_root):
        document = _parse(path, resolved_root)
        cache[document.path] = document.anchors
        for link in document.links:
            if link.image and not check_images:
                continue
            if link.reference:
                problem = (
                    None
                    if link.value in document.definitions
                    else f"link reference is not defined: [{link.value}]"
                )
            else:
                problem = _link_problem(document, link, resolved_root, cache)
            if problem is not None:
                problems.append(f"{document.relative}:{link.line}: {problem}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Directory holding the Markdown tree to check",
    )
    parser.add_argument(
        "--check-images",
        action="store_true",
        help="Resolve image targets as well as link targets",
    )
    args = parser.parse_args(argv)
    try:
        problems = check_markdown_links(args.root, args.check_images)
    except MarkdownScanError as exc:
        print(f"markdown link check failed: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"{len(problems)} broken markdown link(s)", file=sys.stderr)
        return 1
    print("every local markdown link resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
