"""Versions, closed vocabularies, and the canonical form of the evidence graph.

The envelope described here is a *serialization* contract, not the in-memory
layout. Materialising it per node in memory was measured at 1,417 bytes a node
against 211 for the lean representation in :mod:`adduce.aeg.nodes` — 522 MB
against 78 MB for the 386,146 call sites of the largest corpus repository,
which on its own would exceed the 512 MB service level for a whole run. So a
node is held lean and its envelope is built once, when it is written or
rendered.

Every vocabulary here is closed. ``resolution_method`` in particular is
load-bearing rather than descriptive: a rule or reporter can filter on it, so
"never let a learned method drive a verdict" is a checkable property of the
data rather than a convention in prose.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

#: Bumped when a field is removed or an existing field changes meaning. A
#: reader that meets a higher major refuses; it never guesses.
AEG_SCHEMA_VERSION = 1

#: Per-node version, so one node type can gain a field without moving the
#: graph-level major.
NODE_SCHEMA_VERSION = 1

OWNER_BUILTIN = "builtin"


class NodeType(str, Enum):
    """The node vocabulary.

    ``SourceLocation`` is deliberately absent: it is a value carried by a node
    rather than a node of its own, which is what keeps a locator attached to
    the thing it locates and lets identity stay free of line numbers.
    """

    PAPER_CLAIM = "PaperClaim"
    METRIC_DEFINITION = "MetricDefinition"
    METRIC_OBSERVATION = "MetricObservation"
    SOURCE_FILE = "SourceFile"
    SYMBOL = "Symbol"
    EXECUTION_COMMAND = "ExecutionCommand"
    RUN_SCRIPT = "RunScript"
    CONFIGURATION_VALUE = "ConfigurationValue"
    CONFIGURATION_SNAPSHOT = "ConfigurationSnapshot"
    DEPENDENCY_DECLARATION = "DependencyDeclaration"
    ENVIRONMENT_CONSTRAINT = "EnvironmentConstraint"
    DATASET_REFERENCE = "DatasetReference"
    MODEL_REFERENCE = "ModelReference"
    CHECKPOINT_REFERENCE = "CheckpointReference"
    SEED_OPERATION = "SeedOperation"
    HARDWARE_REQUIREMENT = "HardwareRequirement"
    REMOTE_ARTIFACT = "RemoteArtifact"
    REPOSITORY_COMMIT = "RepositoryCommit"
    GENERATED_RESULT = "GeneratedResult"


class EdgeType(str, Enum):
    """The edge vocabulary.

    ``CONTRADICTS`` and ``MAY_SUPPORT`` are first-class relations, not error
    states. ``MAY_SUPPORT`` is the abstention edge: it records "this is the
    best candidate and it is not good enough to assert", which is the judgement
    the current system has no way to express.
    """

    CLAIMS = "CLAIMS"
    PRODUCED_BY = "PRODUCED_BY"
    CONFIGURED_BY = "CONFIGURED_BY"
    EXECUTED_BY = "EXECUTED_BY"
    READS = "READS"
    WRITES = "WRITES"
    DEPENDS_ON = "DEPENDS_ON"
    DERIVED_FROM = "DERIVED_FROM"
    VERSIONED_AT = "VERSIONED_AT"
    REPORTED_IN = "REPORTED_IN"
    EVALUATED_ON = "EVALUATED_ON"
    PINNED_TO = "PINNED_TO"
    CONTRADICTS = "CONTRADICTS"
    MAY_SUPPORT = "MAY_SUPPORT"


class ResolutionMethod(str, Enum):
    """How a fact was established. Closed, and filterable by design."""

    DIRECT_PARSE = "direct_parse"
    AST_RESOLVED = "ast_resolved"
    ALIAS_RESOLVED = "alias_resolved"
    WRAPPER_RESOLVED = "wrapper_resolved"
    LEXICAL_MATCH = "lexical_match"
    GRAPH_MATCH = "graph_match"
    NUMERIC_RECONCILIATION = "numeric_reconciliation"
    AUTHOR_DECLARED = "author_declared"
    ONLINE_RESOLVED = "online_resolved"
    DYNAMIC_OBSERVED = "dynamic_observed"
    MODEL_RANKED = "model_ranked"


class UncertaintyKind(str, Enum):
    """The shape of an unknown, not merely its magnitude.

    ``NOT_EXAMINED`` is distinct from absence. A file skipped for size and a
    file that does not exist are the same silence today, which is a silent
    partial result.
    """

    UNRESOLVABLE_DYNAMIC = "unresolvable_dynamic"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    PARSE_FAILED = "parse_failed"
    NOT_EXAMINED = "not_examined"


#: The only methods that may carry full confidence. A parsed scalar and an
#: author's own declaration are facts; everything else is an inference.
CERTAIN_METHODS = frozenset({ResolutionMethod.DIRECT_PARSE, ResolutionMethod.AUTHOR_DECLARED})

#: Methods that may never raise a link above the abstention threshold on their
#: own, nor contribute to a passing verdict.
UNTRUSTED_METHODS = frozenset({ResolutionMethod.MODEL_RANKED})

_MAX_VALUE_DEPTH = 4


class SchemaError(ValueError):
    """A node, edge, or graph violates the schema."""


class UnsupportedSchemaError(SchemaError):
    """A stored graph declares a major version this build cannot read.

    Refusing is deliberate and follows the manifest precedent: an unsupported
    schema is a hard error, never a silent skip.
    """


def enum_value(member: Any) -> str:
    """The wire form of a vocabulary member, or a preserved unknown string.

    Always prefer this to ``str(member)``. For a ``(str, Enum)`` mixin the
    result of ``str`` differs across the supported Python versions, and this
    package's whole output is compared byte for byte.
    """
    if isinstance(member, Enum):
        value = member.value
        if not isinstance(value, str):  # pragma: no cover - vocabularies are strings
            raise SchemaError(f"vocabulary member {member!r} is not a string")
        return value
    if isinstance(member, str):
        return member
    raise SchemaError(f"expected a vocabulary member or string, got {type(member).__name__}")


def canonical_json(payload: Any) -> str:
    """One line of canonical JSON: sorted keys, no NaN, no incidental spacing."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def validate_value(payload: Any, *, depth: int = 0) -> None:
    """Require a node payload that canonical JSON can represent exactly.

    Checked at construction rather than at write time so the offending
    producer is named, not the serializer.
    """
    if depth > _MAX_VALUE_DEPTH:
        raise SchemaError(f"node value nests deeper than {_MAX_VALUE_DEPTH} levels")
    if payload is None or isinstance(payload, (str, bool, int)):
        return
    if isinstance(payload, float):
        if payload != payload or payload in (float("inf"), float("-inf")):
            raise SchemaError("node value holds a non-finite number")
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            if not isinstance(key, str):
                raise SchemaError(f"node value key {key!r} is not a string")
            validate_value(item, depth=depth + 1)
        return
    if isinstance(payload, (list, tuple)):
        for item in payload:
            validate_value(item, depth=depth + 1)
        return
    raise SchemaError(f"node value holds an unrepresentable {type(payload).__name__}")


def require_supported_major(declared: int, *, label: str) -> None:
    """Refuse a graph from a future major with a structured error."""
    if not isinstance(declared, int):
        raise SchemaError(f"{label} declares a non-integer schema version")
    if declared > AEG_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"{label} declares aeg schema version {declared}; this build reads {AEG_SCHEMA_VERSION}"
        )
    if declared < 1:
        raise SchemaError(f"{label} declares an invalid schema version {declared}")
