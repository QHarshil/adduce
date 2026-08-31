"""Reading and writing a graph as canonical JSONL.

One node or edge per line, so a single entry can be streamed and hashed on its
own, and a diff between two runs reads as a diff. The directory is named by the
graph's own content id, which makes a write idempotent: identical content
produces identical bytes at an identical path, so re-writing is a no-op rather
than an overwrite.

The repository is untrusted and it controls ``.adduce/``. Nothing here accepts
a stored graph on faith — every entry is re-validated, every node is re-hashed
against its recorded content id, and a graph from a future major is refused
outright rather than partially understood.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..safe_write import (
    SafeWriteError,
    create_text_exclusive,
    ensure_safe_directory,
    ensure_safe_directory_tree,
    read_text_regular,
    regular_file_exists,
)
from .graph import Graph
from .nodes import edge_from_envelope, node_from_envelope
from .schema import (
    AEG_SCHEMA_VERSION,
    SchemaError,
    canonical_json,
    require_supported_major,
)

HEADER_NAME = "graph.json"
NODES_NAME = "nodes.jsonl"
EDGES_NAME = "edges.jsonl"

_FILE_MODE = 0o600
_MAX_ENTRY_BYTES = 1_000_000


def graph_directory(root: Path, content_id: str) -> Path:
    """Where a graph with this content id lives under ``root``."""
    digest = content_id.split(":", 1)[-1]
    if not digest or not all(character in "0123456789abcdef" for character in digest):
        raise SchemaError(f"refusing an unrecognised graph content id {content_id!r}")
    return root / ".adduce" / "aeg" / digest


def render_nodes(graph: Graph) -> str:
    return "".join(f"{canonical_json(node.envelope())}\n" for node in graph.nodes)


def render_edges(graph: Graph) -> str:
    return "".join(f"{canonical_json(edge.envelope())}\n" for edge in graph.edges)


def render_header(graph: Graph) -> str:
    return (
        canonical_json(
            {
                "aeg_schema_version": graph.schema_version,
                "content_id": graph.content_id,
                "counts_by_type": graph.counts_by_type(),
                "edge_count": len(graph.edges),
                "node_count": len(graph),
            }
        )
        + "\n"
    )


def write_graph(graph: Graph, root: Path) -> Path:
    """Write ``graph`` under ``root``, returning its directory.

    A graph already present at its content-addressed path is left untouched:
    the bytes it would be given are the bytes it already holds.
    """
    directory = graph_directory(root, graph.content_id)
    ensure_safe_directory_tree(directory, label="evidence graph directory")
    header = directory / HEADER_NAME
    if regular_file_exists(header, label="evidence graph header"):
        return directory
    create_text_exclusive(
        directory / NODES_NAME,
        render_nodes(graph),
        label="evidence graph nodes",
        exact_mode=_FILE_MODE,
    )
    create_text_exclusive(
        directory / EDGES_NAME,
        render_edges(graph),
        label="evidence graph edges",
        exact_mode=_FILE_MODE,
    )
    create_text_exclusive(
        header,
        render_header(graph),
        label="evidence graph header",
        exact_mode=_FILE_MODE,
    )
    return directory


def _read(directory: Path, name: str, label: str) -> str | None:
    return read_text_regular(
        directory / name,
        label=label,
        parent_label="evidence graph directory",
    )


def _entries(text: str, label: str) -> list[dict[str, Any]]:
    entries = []
    # Same reason as the collectors: the number in an error message has to be
    # the line a reader counts to.
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        if len(line) > _MAX_ENTRY_BYTES:
            raise SchemaError(f"{label} line {number} exceeds {_MAX_ENTRY_BYTES} bytes")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SchemaError(f"{label} line {number} is not JSON") from exc
        if not isinstance(payload, dict):
            raise SchemaError(f"{label} line {number} is not an object")
        entries.append(payload)
    return entries


def read_graph(directory: Path) -> Graph | None:
    """Load a graph, or return ``None`` when none is stored at ``directory``.

    A malformed or tampered graph raises rather than returning a partial one.
    The caller's correct response is to rebuild, never to proceed with what
    could be read.
    """
    if not ensure_safe_directory(directory, label="evidence graph directory"):
        return None
    try:
        header_text = _read(directory, HEADER_NAME, "evidence graph header")
    except SafeWriteError:
        return None
    if header_text is None:
        return None

    try:
        header = json.loads(header_text)
    except json.JSONDecodeError as exc:
        raise SchemaError("evidence graph header is not JSON") from exc
    if not isinstance(header, dict):
        raise SchemaError("evidence graph header is not an object")
    declared = header.get("aeg_schema_version", AEG_SCHEMA_VERSION)
    require_supported_major(declared, label="evidence graph")

    graph = Graph(schema_version=declared)
    nodes_text = _read(directory, NODES_NAME, "evidence graph nodes") or ""
    edges_text = _read(directory, EDGES_NAME, "evidence graph edges") or ""
    for payload in _entries(nodes_text, "evidence graph nodes"):
        graph.add(node_from_envelope(payload))
    for payload in _entries(edges_text, "evidence graph edges"):
        graph.add_edge(edge_from_envelope(payload))

    recorded = header.get("content_id")
    if isinstance(recorded, str) and recorded != graph.content_id:
        raise SchemaError("evidence graph contents do not match the recorded header digest")
    return graph
