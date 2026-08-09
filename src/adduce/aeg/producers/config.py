"""Configuration nodes: the values a repository declares, and what it runs them with.

Chosen as the first producer because it is small and because it is the only
small one with a genuine mixed-confidence case. A parsed scalar is a fact and
carries full confidence. "This is a Hydra project" is not: it is inferred from
a ``defaults`` key next to a directory called ``conf``, which is a hint wearing
the clothes of a parse. The graph records the difference in
``resolution_method`` so a rule or a reporter can act on it, rather than
leaving the distinction in a comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...naming import canonical_hyperparameter
from ..graph import Graph
from ..nodes import Edge, Node, Provenance, SourceLocation
from ..schema import EdgeType, NodeType, ResolutionMethod
from . import analyzer_version

if TYPE_CHECKING:  # pragma: no cover
    from ...evidence import Evidence

#: Confidence for anything this producer infers rather than parses. One value
#: for every lexical match, deliberately: the distinctions between weak signals
#: are not measured yet, and inventing a spread would present a guess as a
#: calibration. The calibration table that replaces this is Phase 3's.
LEXICAL_CONFIDENCE = 0.5

_HYDRA_DISTRIBUTION = "hydra-core"
_DEEPSPEED_DISTRIBUTION = "deepspeed"


def snapshot_id(path: str) -> str:
    return f"configsnapshot:{path}"


def value_id(path: str, key: str) -> str:
    return f"configvalue:{path}#{key}"


def dependency_id(distribution: str) -> str:
    return f"dependency:{distribution}"


class ConfigProducer:
    """Emits one snapshot per configuration file and one node per declared key."""

    name: str = "adduce.aeg.producers.config"
    version: int = 1
    produces: tuple[NodeType, ...] = (
        NodeType.CONFIGURATION_SNAPSHOT,
        NodeType.CONFIGURATION_VALUE,
        NodeType.DEPENDENCY_DECLARATION,
    )

    def run(self, graph: Graph, evidence: Evidence) -> None:
        version = analyzer_version()
        parsed = graph.provenance(
            Provenance(
                producer=self.name,
                producer_version=self.version,
                resolution_method=ResolutionMethod.DIRECT_PARSE,
                analyzer_version=version,
                parser="yaml.safe_load|json.loads|tomllib.loads",
                parser_version="stdlib+pyyaml",
            )
        )
        inferred = graph.provenance(
            Provenance(
                producer=self.name,
                producer_version=self.version,
                resolution_method=ResolutionMethod.LEXICAL_MATCH,
                analyzer_version=version,
                parser="yaml.safe_load|json.loads|tomllib.loads",
                parser_version="stdlib+pyyaml",
            )
        )

        for config in evidence.config.files:
            location = (SourceLocation(config.path),)
            graph.add(
                Node(
                    logical_id=snapshot_id(config.path),
                    type=NodeType.CONFIGURATION_SNAPSHOT,
                    value={"path": config.path, "key_count": len(config.values)},
                    provenance=parsed,
                    locations=location,
                )
            )
            for key in sorted(config.values):
                self._add_value(graph, parsed, config.path, key, config.values[key], location)
            if config.is_hydra:
                self._add_inference(graph, inferred, config.path, _HYDRA_DISTRIBUTION)
            if config.is_deepspeed:
                self._add_inference(graph, inferred, config.path, _DEEPSPEED_DISTRIBUTION)

    def _add_value(
        self,
        graph: Graph,
        provenance: Provenance,
        path: str,
        key: str,
        scalar: Any,
        location: tuple[SourceLocation, ...],
    ) -> None:
        value: dict[str, Any] = {"key": key, "scalar": scalar}
        canonical = canonical_hyperparameter(key)
        if canonical:
            value["canonical_name"] = canonical
        graph.add(
            Node(
                logical_id=value_id(path, key),
                type=NodeType.CONFIGURATION_VALUE,
                value=value,
                provenance=provenance,
                locations=location,
            )
        )
        graph.add_edge(
            Edge(
                source=value_id(path, key),
                type=EdgeType.REPORTED_IN,
                target=snapshot_id(path),
                provenance=provenance,
            )
        )

    def _add_inference(
        self, graph: Graph, provenance: Provenance, path: str, distribution: str
    ) -> None:
        """Record a configuration system as an inference, never as a parse.

        The node carries no location: the dependency is a property of the
        repository, and more than one file can imply it. What is located is the
        *evidence* for it, which is the edge from a snapshot that has a path.
        """
        graph.add(
            Node(
                logical_id=dependency_id(distribution),
                type=NodeType.DEPENDENCY_DECLARATION,
                value={"distribution": distribution, "role": "configuration_system"},
                provenance=provenance,
                confidence=LEXICAL_CONFIDENCE,
            )
        )
        graph.add_edge(
            Edge(
                source=snapshot_id(path),
                type=EdgeType.DEPENDS_ON,
                target=dependency_id(distribution),
                provenance=provenance,
                confidence=LEXICAL_CONFIDENCE,
            )
        )
