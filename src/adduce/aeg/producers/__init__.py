"""Producers: the only things that may put nodes into the graph.

A producer declares what it emits, so the set of producers can be scheduled
rather than ordered by the accident of call sequence, and so a plugin's nodes
can be attributed and dropped without discarding the graph.

During the migration a producer reads today's collected evidence. That arrow
inverts once the collectors themselves emit nodes: at that point ``evidence``
becomes an adapter that materialises the existing dataclasses *from* the graph,
which is what keeps all 78 rules and every third-party rule working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ... import __version__
from ...evidence.portability import secret_kind
from ..graph import Graph
from ..schema import NodeType

if TYPE_CHECKING:  # pragma: no cover - import inverts once collectors emit nodes
    from ...evidence import Evidence


@runtime_checkable
class Producer(Protocol):
    """One source of nodes, versioned so a cache can tell its output apart."""

    name: str
    version: int
    produces: tuple[NodeType, ...]

    def run(self, graph: Graph, evidence: Evidence) -> None:
        """Emit this producer's nodes and edges into ``graph``."""


def builtin_producers() -> tuple[Producer, ...]:
    from .config import ConfigProducer
    from .python import PythonProducer

    return (ConfigProducer(), PythonProducer())


def produce(evidence: Evidence, *, producers: tuple[Producer, ...] | None = None) -> Graph:
    """Build a graph from collected evidence.

    Producers run in declared order and may not overwrite one another; two
    producers claiming the same identity with different content is an error,
    not a last-writer-wins race.
    """
    graph = Graph()
    for producer in producers if producers is not None else builtin_producers():
        producer.run(graph, evidence)
    return graph


def analyzer_version() -> str:
    return __version__


def guarded_scalar(field: str, scalar: Any) -> dict[str, Any]:
    """A node value fragment carrying ``scalar``, or a record that it was withheld.

    A producer reads a repository's own bytes, so any value it copies can be a
    committed credential. A value the portability detector recognises never
    enters the graph, in memory, in a rendered format, or on disk. What takes
    its place names the withheld field and the kind it matched, so a reader can
    tell a redaction from an absence without the value being echoed to do it.
    The detection is the one in :mod:`adduce.evidence.portability`; the graph
    withholds exactly what ``R-PORT-004`` reports and nothing else.
    """
    kind = secret_kind(scalar)
    if kind is None:
        return {field: scalar}
    return {"redacted": field, "secret_kind": kind}
