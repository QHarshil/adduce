"""The lean in-memory node and edge, and their envelope form.

Representation was chosen on measurement, not on taste. Converting real call
sites into each candidate gave, per node: 1,417 bytes for the envelope held
literally as nested dicts, 673 for a slotted node with shared provenance, and
211 for the form here — slotted, strings interned by the graph, provenance
shared as a flyweight, and ``content_id`` derived on demand rather than stored.
Today's untyped ``CallSite`` costs 493. So the graph can carry the same facts
for less than half of what the current evidence objects cost, and the envelope
is built only at the boundary.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .schema import (
    CERTAIN_METHODS,
    NODE_SCHEMA_VERSION,
    OWNER_BUILTIN,
    EdgeType,
    NodeType,
    ResolutionMethod,
    SchemaError,
    UncertaintyKind,
    canonical_json,
    enum_value,
    validate_value,
)

_OWNER_RE = re.compile(r"^(builtin|plugin:[A-Za-z0-9._-]+)$")
_CERTAIN_VALUES = frozenset(method.value for method in CERTAIN_METHODS)


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where a fact was read. Lines are data, never part of an identity."""

    path: str
    line: int | None = None
    end_line: int | None = None

    def envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.line is not None:
            payload["line"] = self.line
        if self.end_line is not None:
            payload["end_line"] = self.end_line
        return payload


@dataclass(frozen=True, slots=True)
class Uncertainty:
    """What is unknown about a node, in a form a reporter can explain."""

    kind: UncertaintyKind | str
    detail: str | None = None
    candidates: tuple[str, ...] = ()

    def envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": enum_value(self.kind)}
        if self.detail is not None:
            payload["detail"] = self.detail
        if self.candidates:
            payload["candidates"] = list(self.candidates)
        return payload


@dataclass(frozen=True, slots=True)
class Provenance:
    """Who produced a fact, from what, and by which method.

    Shared between every node a producer emits for one file, which is what
    keeps its cost off the per-node budget.
    """

    producer: str
    producer_version: int
    resolution_method: ResolutionMethod | str
    analyzer_version: str
    parser: str | None = None
    parser_version: str | None = None
    inputs: tuple[str, ...] = ()

    def envelope(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "producer_version": self.producer_version,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "resolution_method": enum_value(self.resolution_method),
            "inputs": list(self.inputs),
            "analyzer_version": self.analyzer_version,
        }


def _validate_common(logical_id: str, owner: str, confidence: float, provenance: Provenance) -> None:
    if not logical_id or "\n" in logical_id or "\r" in logical_id:
        raise SchemaError(f"invalid logical id {logical_id!r}")
    if not _OWNER_RE.match(owner):
        raise SchemaError(f"invalid owner {owner!r}; expected 'builtin' or 'plugin:<dist>'")
    if not 0.0 <= confidence <= 1.0:
        raise SchemaError(f"confidence {confidence} is outside [0.0, 1.0]")
    if confidence == 1.0 and enum_value(provenance.resolution_method) not in _CERTAIN_VALUES:
        raise SchemaError(
            f"resolution method {enum_value(provenance.resolution_method)!r} cannot carry "
            "full confidence; only a parsed fact or an author declaration may"
        )


@dataclass(frozen=True, slots=True)
class Node:
    """One fact, identified twice: logically, and by content.

    ``logical_id`` is stable across content edits and never encodes a line
    number, so a locator survives a reformat. ``content_id`` covers everything
    except provenance and confidence, so re-running an unchanged producer over
    unchanged bytes yields the same identity.
    """

    logical_id: str
    type: NodeType | str
    value: Mapping[str, Any]
    provenance: Provenance
    locations: tuple[SourceLocation, ...] = ()
    confidence: float = 1.0
    uncertainty: Uncertainty | None = None
    owner: str = OWNER_BUILTIN
    schema_version: int = NODE_SCHEMA_VERSION
    _content_id: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        _validate_common(self.logical_id, self.owner, self.confidence, self.provenance)
        validate_value(dict(self.value))

    @property
    def content_id(self) -> str:
        """``sha256`` over the node's canonical form, excluding provenance and confidence."""
        if self._content_id is None:
            digest = hashlib.sha256(canonical_json(self._identity()).encode("utf-8")).hexdigest()
            object.__setattr__(self, "_content_id", f"sha256:{digest}")
        assert self._content_id is not None
        return self._content_id

    def _identity(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "type": enum_value(self.type),
            "schema_version": self.schema_version,
            "value": dict(self.value),
            "locations": [location.envelope() for location in self.locations],
            "uncertainty": self.uncertainty.envelope() if self.uncertainty else None,
            "owner": self.owner,
        }

    def envelope(self) -> dict[str, Any]:
        payload = self._identity()
        payload["content_id"] = self.content_id
        payload["provenance"] = self.provenance.envelope()
        payload["confidence"] = self.confidence
        return payload


@dataclass(frozen=True, slots=True)
class Edge:
    """A typed, provenance-carrying relation between two logical ids."""

    source: str
    type: EdgeType | str
    target: str
    provenance: Provenance
    confidence: float = 1.0
    uncertainty: Uncertainty | None = None
    owner: str = OWNER_BUILTIN
    schema_version: int = NODE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_common(self.source, self.owner, self.confidence, self.provenance)
        if not self.target or "\n" in self.target or "\r" in self.target:
            raise SchemaError(f"invalid edge target {self.target!r}")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.source, enum_value(self.type), self.target, self.owner)

    def envelope(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "type": enum_value(self.type),
            "target": self.target,
            "schema_version": self.schema_version,
            "provenance": self.provenance.envelope(),
            "confidence": self.confidence,
            "uncertainty": self.uncertainty.envelope() if self.uncertainty else None,
            "owner": self.owner,
        }


def _known(vocabulary: type[NodeType] | type[EdgeType], raw: str) -> Any:
    """Map a wire string to its vocabulary member, preserving an unknown one.

    A reader that meets a node type it does not know keeps it and ignores it.
    Dropping it would silently discard a plugin's evidence; erroring would let
    one bad plugin destroy the whole graph.
    """
    try:
        return vocabulary(raw)
    except ValueError:
        return raw


def provenance_from_envelope(payload: Mapping[str, Any]) -> Provenance:
    raw_method = payload.get("resolution_method")
    if not isinstance(raw_method, str):
        raise SchemaError("provenance is missing its resolution method")
    try:
        method: ResolutionMethod | str = ResolutionMethod(raw_method)
    except ValueError:
        method = raw_method
    inputs = payload.get("inputs") or []
    if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
        raise SchemaError("provenance inputs must be a list of strings")
    producer_version = payload.get("producer_version")
    if not isinstance(producer_version, int):
        raise SchemaError("provenance is missing an integer producer version")
    return Provenance(
        producer=str(payload.get("producer", "")),
        producer_version=producer_version,
        resolution_method=method,
        analyzer_version=str(payload.get("analyzer_version", "")),
        parser=payload.get("parser"),
        parser_version=payload.get("parser_version"),
        inputs=tuple(inputs),
    )


def _uncertainty_from_envelope(payload: Any) -> Uncertainty | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise SchemaError("uncertainty must be a mapping")
    raw_kind = payload.get("kind")
    if not isinstance(raw_kind, str):
        raise SchemaError("uncertainty is missing its kind")
    try:
        kind: UncertaintyKind | str = UncertaintyKind(raw_kind)
    except ValueError:
        kind = raw_kind
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list):
        raise SchemaError("uncertainty candidates must be a list")
    return Uncertainty(
        kind=kind,
        detail=payload.get("detail"),
        candidates=tuple(str(item) for item in candidates),
    )


def _locations_from_envelope(payload: Any) -> tuple[SourceLocation, ...]:
    if not payload:
        return ()
    if not isinstance(payload, list):
        raise SchemaError("locations must be a list")
    locations = []
    for item in payload:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise SchemaError("every location needs a string path")
        locations.append(
            SourceLocation(
                path=item["path"],
                line=item.get("line"),
                end_line=item.get("end_line"),
            )
        )
    return tuple(locations)


def node_from_envelope(payload: Mapping[str, Any]) -> Node:
    """Rebuild a node from its wire form, refusing anything malformed.

    A repository controls the directory a graph is read from, so nothing here
    trusts the file: an entry that does not validate is an error the caller
    turns into a rebuild, never a node that quietly enters the graph.
    """
    for required in ("logical_id", "type", "provenance"):
        if required not in payload:
            raise SchemaError(f"node envelope is missing {required!r}")
    logical_id = payload["logical_id"]
    if not isinstance(logical_id, str):
        raise SchemaError("node logical id must be a string")
    raw_type = payload["type"]
    if not isinstance(raw_type, str):
        raise SchemaError("node type must be a string")
    value = payload.get("value") or {}
    if not isinstance(value, Mapping):
        raise SchemaError("node value must be a mapping")
    provenance_payload = payload["provenance"]
    if not isinstance(provenance_payload, Mapping):
        raise SchemaError("node provenance must be a mapping")
    confidence = payload.get("confidence", 1.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise SchemaError("node confidence must be a number")
    schema_version = payload.get("schema_version", NODE_SCHEMA_VERSION)
    if not isinstance(schema_version, int):
        raise SchemaError("node schema version must be an integer")

    node = Node(
        logical_id=logical_id,
        type=_known(NodeType, raw_type),
        value=dict(value),
        provenance=provenance_from_envelope(provenance_payload),
        locations=_locations_from_envelope(payload.get("locations")),
        confidence=float(confidence),
        uncertainty=_uncertainty_from_envelope(payload.get("uncertainty")),
        owner=str(payload.get("owner", OWNER_BUILTIN)),
        schema_version=schema_version,
    )
    stored = payload.get("content_id")
    if isinstance(stored, str) and stored != node.content_id:
        raise SchemaError(
            f"node {logical_id!r} does not hash to its recorded content id; the graph is not intact"
        )
    return node


def edge_from_envelope(payload: Mapping[str, Any]) -> Edge:
    for required in ("source", "type", "target", "provenance"):
        if required not in payload:
            raise SchemaError(f"edge envelope is missing {required!r}")
    source, raw_type, target = payload["source"], payload["type"], payload["target"]
    if not isinstance(source, str) or not isinstance(target, str) or not isinstance(raw_type, str):
        raise SchemaError("edge source, type, and target must be strings")
    provenance_payload = payload["provenance"]
    if not isinstance(provenance_payload, Mapping):
        raise SchemaError("edge provenance must be a mapping")
    confidence = payload.get("confidence", 1.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise SchemaError("edge confidence must be a number")
    schema_version = payload.get("schema_version", NODE_SCHEMA_VERSION)
    if not isinstance(schema_version, int):
        raise SchemaError("edge schema version must be an integer")
    return Edge(
        source=source,
        type=_known(EdgeType, raw_type),
        target=target,
        provenance=provenance_from_envelope(provenance_payload),
        confidence=float(confidence),
        uncertainty=_uncertainty_from_envelope(payload.get("uncertainty")),
        owner=str(payload.get("owner", OWNER_BUILTIN)),
        schema_version=schema_version,
    )
