"""The Artifact Evidence Graph: typed, versioned, provenance-carrying evidence.

Every fact carries where it came from, how it was established, and how much
that method is worth. ``resolution_method`` is the load-bearing field: it makes
"a learned method never drives a verdict" a property a rule can check rather
than a promise prose makes.

The package is deliberately separate from :mod:`adduce.graph`, which builds
claim trails and keeps its published JSON shape; that module becomes a view
over this one.
"""

from __future__ import annotations

from .graph import Graph, NodeConflictError, OwnershipError
from .nodes import (
    Edge,
    Node,
    Provenance,
    SourceLocation,
    Uncertainty,
    edge_from_envelope,
    node_from_envelope,
)
from .schema import (
    AEG_SCHEMA_VERSION,
    CERTAIN_METHODS,
    NODE_SCHEMA_VERSION,
    OWNER_BUILTIN,
    UNTRUSTED_METHODS,
    EdgeType,
    NodeType,
    ResolutionMethod,
    SchemaError,
    UncertaintyKind,
    UnsupportedSchemaError,
    canonical_json,
    enum_value,
)

__all__ = [
    "AEG_SCHEMA_VERSION",
    "CERTAIN_METHODS",
    "NODE_SCHEMA_VERSION",
    "OWNER_BUILTIN",
    "UNTRUSTED_METHODS",
    "Edge",
    "EdgeType",
    "Graph",
    "Node",
    "NodeConflictError",
    "NodeType",
    "OwnershipError",
    "Provenance",
    "ResolutionMethod",
    "SchemaError",
    "SourceLocation",
    "Uncertainty",
    "UncertaintyKind",
    "UnsupportedSchemaError",
    "canonical_json",
    "edge_from_envelope",
    "enum_value",
    "node_from_envelope",
]
