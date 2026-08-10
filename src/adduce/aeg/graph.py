"""The graph itself: insertion with ownership, derived indexes, read tracking.

Indexes are derived and never persisted as truth, so a stored graph cannot
disagree with itself. Insertion is idempotent by content: producing the same
fact twice is free, producing a different fact under the same identity is an
error rather than a silent overwrite — the same discipline as the parse-once
assertion, applied to evidence.

Queries record which nodes they touched. That is what makes a rule-result
cache possible later: invalidation keyed on the nodes a rule actually read,
rather than on "any file changed, so rerun everything".
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .nodes import Edge, Node, Provenance, SourceLocation
from .schema import (
    AEG_SCHEMA_VERSION,
    EdgeType,
    NodeType,
    SchemaError,
    canonical_json,
    enum_value,
)


class OwnershipError(SchemaError):
    """A producer tried to redefine a node another owner had established."""


class NodeConflictError(SchemaError):
    """One owner produced two different facts under the same identity."""


class Graph:
    """A versioned, typed, provenance-carrying evidence graph."""

    def __init__(self, *, schema_version: int = AEG_SCHEMA_VERSION) -> None:
        self.schema_version = schema_version
        self._nodes: dict[str, Node] = {}
        self._edges: dict[tuple[str, str, str, str], Edge] = {}
        self._by_type: dict[str, list[str]] = {}
        self._by_path: dict[str, list[str]] = {}
        self._edges_out: dict[str, list[Edge]] = {}
        self._edges_in: dict[str, list[Edge]] = {}
        self._strings: dict[str, str] = {}
        self._provenances: dict[tuple[Any, ...], Provenance] = {}
        self._locations: dict[tuple[Any, ...], tuple[SourceLocation, ...]] = {}
        self._read_stack: list[set[str]] = []

    # -- insertion ---------------------------------------------------------

    def add(self, node: Node) -> Node:
        """Insert a node, or accept it as already present.

        Returns the node that is in the graph, which for a repeat insertion is
        the one that was already there.
        """
        interned = self._intern_node(node)
        existing = self._nodes.get(interned.logical_id)
        if existing is not None:
            if existing.content_id == interned.content_id:
                return existing
            if existing.owner != interned.owner:
                raise OwnershipError(
                    f"{interned.owner} cannot redefine {interned.logical_id!r}, "
                    f"which belongs to {existing.owner}"
                )
            raise NodeConflictError(
                f"{interned.owner} produced two different facts for {interned.logical_id!r}"
            )
        self._nodes[interned.logical_id] = interned
        self._by_type.setdefault(enum_value(interned.type), []).append(interned.logical_id)
        for location in interned.locations:
            self._by_path.setdefault(location.path, []).append(interned.logical_id)
        return interned

    def add_edge(self, edge: Edge) -> Edge:
        """Insert an edge. Endpoints need not exist yet; resolution is a later pass."""
        interned = self._intern_edge(edge)
        existing = self._edges.get(interned.key)
        if existing is not None:
            return existing
        self._edges[interned.key] = interned
        self._edges_out.setdefault(interned.source, []).append(interned)
        self._edges_in.setdefault(interned.target, []).append(interned)
        return interned

    # -- queries -----------------------------------------------------------

    @property
    def nodes(self) -> tuple[Node, ...]:
        """Every node, ordered by logical id rather than by insertion."""
        return tuple(self._nodes[key] for key in sorted(self._nodes))

    @property
    def edges(self) -> tuple[Edge, ...]:
        return tuple(self._edges[key] for key in sorted(self._edges))

    def node(self, logical_id: str) -> Node | None:
        self._record((logical_id,))
        return self._nodes.get(logical_id)

    def of_type(self, node_type: NodeType | str) -> tuple[Node, ...]:
        ids = sorted(self._by_type.get(enum_value(node_type), ()))
        self._record(ids)
        return tuple(self._nodes[key] for key in ids)

    def at_path(self, path: str) -> tuple[Node, ...]:
        ids = sorted(set(self._by_path.get(path, ())))
        self._record(ids)
        return tuple(self._nodes[key] for key in ids)

    def edges_from(self, logical_id: str, edge_type: EdgeType | str | None = None) -> tuple[Edge, ...]:
        return self._select(self._edges_out.get(logical_id, ()), edge_type)

    def edges_to(self, logical_id: str, edge_type: EdgeType | str | None = None) -> tuple[Edge, ...]:
        return self._select(self._edges_in.get(logical_id, ()), edge_type)

    def _select(self, found: Any, edge_type: EdgeType | str | None) -> tuple[Edge, ...]:
        selected = tuple(found)
        if edge_type is not None:
            wanted = enum_value(edge_type)
            selected = tuple(edge for edge in selected if enum_value(edge.type) == wanted)
        selected = tuple(sorted(selected, key=lambda edge: edge.key))
        self._record([edge.source for edge in selected] + [edge.target for edge in selected])
        return selected

    @contextmanager
    def record_reads(self) -> Iterator[set[str]]:
        """Collect the logical ids read inside the block.

        Nested blocks each see their own reads, and an inner block's reads also
        count for the outer one — a caller that delegates still depends on
        whatever its callee looked at.
        """
        touched: set[str] = set()
        self._read_stack.append(touched)
        try:
            yield touched
        finally:
            self._read_stack.pop()
            if self._read_stack:
                self._read_stack[-1].update(touched)

    def _record(self, ids: Any) -> None:
        if self._read_stack:
            self._read_stack[-1].update(ids)

    # -- identity ----------------------------------------------------------

    @property
    def content_id(self) -> str:
        """A digest over every node and edge, stable across insertion order."""
        payload = canonical_json(
            {
                "aeg_schema_version": self.schema_version,
                "nodes": [node.content_id for node in self.nodes],
                "edges": [canonical_json(edge.envelope()) for edge in self.edges],
            }
        )
        return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def counts_by_type(self) -> dict[str, int]:
        return {name: len(ids) for name, ids in sorted(self._by_type.items())}

    def __len__(self) -> int:
        return len(self._nodes)

    # -- interning ---------------------------------------------------------

    def _string(self, text: str) -> str:
        found = self._strings.get(text)
        if found is None:
            self._strings[text] = text
            return text
        return found

    def provenance(self, provenance: Provenance) -> Provenance:
        """Return the shared instance equal to ``provenance``.

        Producers should hold the result and reuse it across every node they
        emit for one file; that sharing is most of the difference between the
        measured 211 bytes a node and 673.
        """
        key = (
            provenance.producer,
            provenance.producer_version,
            enum_value(provenance.resolution_method),
            provenance.analyzer_version,
            provenance.parser,
            provenance.parser_version,
            provenance.inputs,
        )
        found = self._provenances.get(key)
        if found is None:
            self._provenances[key] = provenance
            return provenance
        return found

    def locations(self, locations: tuple[SourceLocation, ...]) -> tuple[SourceLocation, ...]:
        """Return the shared tuple equal to ``locations``.

        Every node a producer emits for one file usually cites that one file,
        so without pooling a repository's nodes each carry their own copy of
        the same locator. Measured on 12,176 configuration values over 203
        files: 12,176 distinct location tuples where 203 were needed.
        """
        key = tuple((location.path, location.line, location.end_line) for location in locations)
        found = self._locations.get(key)
        if found is None:
            shared = tuple(
                SourceLocation(self._string(location.path), location.line, location.end_line)
                for location in locations
            )
            self._locations[key] = shared
            return shared
        return found

    def _intern_node(self, node: Node) -> Node:
        return Node(
            logical_id=self._string(node.logical_id),
            type=node.type,
            value=self._intern_value(dict(node.value)),
            provenance=self.provenance(node.provenance),
            locations=self.locations(node.locations),
            confidence=node.confidence,
            uncertainty=node.uncertainty,
            owner=self._string(node.owner),
            schema_version=node.schema_version,
        )

    def _intern_edge(self, edge: Edge) -> Edge:
        return Edge(
            source=self._string(edge.source),
            type=edge.type,
            target=self._string(edge.target),
            provenance=self.provenance(edge.provenance),
            confidence=edge.confidence,
            uncertainty=edge.uncertainty,
            owner=self._string(edge.owner),
            schema_version=edge.schema_version,
        )

    def _intern_value(self, value: dict[str, Any]) -> dict[str, Any]:
        interned: dict[str, Any] = {}
        for key, item in value.items():
            interned[self._string(key)] = self._string(item) if isinstance(item, str) else item
        return interned
