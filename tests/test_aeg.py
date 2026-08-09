"""The evidence graph: identity, honesty, ownership, and what survives a write.

Four of these guard properties that prose cannot enforce. A node may carry full
confidence only when it was parsed or declared, so an inference cannot dress
itself as a fact. A stored graph is re-hashed on load, so a repository cannot
hand back a tampered one. One owner cannot redefine another's node, so a broken
plugin costs its own evidence and no one else's. And the wire form of a
vocabulary member is its value on every supported interpreter, because
``str()`` over a ``(str, Enum)`` mixin is not stable across them and this
package's whole output is compared byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adduce.aeg import (
    AEG_SCHEMA_VERSION,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeConflictError,
    NodeType,
    OwnershipError,
    Provenance,
    ResolutionMethod,
    SchemaError,
    SourceLocation,
    Uncertainty,
    UncertaintyKind,
    UnsupportedSchemaError,
    canonical_json,
    enum_value,
    node_from_envelope,
)
from adduce.aeg.producers import produce
from adduce.aeg.producers.config import LEXICAL_CONFIDENCE
from adduce.aeg.store import (
    HEADER_NAME,
    NODES_NAME,
    read_graph,
    render_nodes,
    write_graph,
)
from adduce.cli import app
from adduce.evidence import collect
from adduce.model import scan_repository
from tests.conftest import plain

PARSED = Provenance(
    producer="test",
    producer_version=1,
    resolution_method=ResolutionMethod.DIRECT_PARSE,
    analyzer_version="0.0.0",
)
GUESSED = Provenance(
    producer="test",
    producer_version=1,
    resolution_method=ResolutionMethod.LEXICAL_MATCH,
    analyzer_version="0.0.0",
)


def a_node(logical_id: str = "configvalue:a.yaml#lr", **overrides: object) -> Node:
    fields: dict[str, object] = {
        "logical_id": logical_id,
        "type": NodeType.CONFIGURATION_VALUE,
        "value": {"key": "lr", "scalar": 0.1},
        "provenance": PARSED,
        "locations": (SourceLocation("a.yaml", 3),),
    }
    fields.update(overrides)
    return Node(**fields)  # type: ignore[arg-type]


# -- identity --------------------------------------------------------------


def test_content_id_ignores_provenance_and_confidence() -> None:
    """Re-running a producer over unchanged bytes yields the same identity."""
    parsed = a_node()
    relabelled = a_node(
        provenance=Provenance(
            producer="other",
            producer_version=9,
            resolution_method=ResolutionMethod.DIRECT_PARSE,
            analyzer_version="9.9.9",
        )
    )
    assert parsed.content_id == relabelled.content_id
    assert parsed.content_id != a_node(value={"key": "lr", "scalar": 0.2}).content_id


def test_content_id_covers_the_location_but_the_logical_id_does_not() -> None:
    """A locator moves under a reformat; an identity must not."""
    moved = a_node(locations=(SourceLocation("a.yaml", 400),))
    assert moved.logical_id == a_node().logical_id
    assert moved.content_id != a_node().content_id


def test_graph_content_id_is_independent_of_insertion_order() -> None:
    first, second = Graph(), Graph()
    first.add(a_node("configvalue:a.yaml#lr"))
    first.add(a_node("configvalue:a.yaml#wd"))
    second.add(a_node("configvalue:a.yaml#wd"))
    second.add(a_node("configvalue:a.yaml#lr"))
    assert first.content_id == second.content_id


# -- honesty ---------------------------------------------------------------


def test_an_inference_may_not_carry_full_confidence() -> None:
    with pytest.raises(SchemaError, match="cannot carry"):
        a_node(provenance=GUESSED, confidence=1.0)


def test_a_parsed_fact_may() -> None:
    assert a_node(provenance=PARSED, confidence=1.0).confidence == 1.0


def test_an_inference_below_full_confidence_is_accepted() -> None:
    assert a_node(provenance=GUESSED, confidence=0.5).confidence == 0.5


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_the_unit_interval_is_refused(confidence: float) -> None:
    with pytest.raises(SchemaError, match="outside"):
        a_node(confidence=confidence)


def test_an_unknown_resolution_method_cannot_claim_certainty() -> None:
    """A method this build does not know is an inference until proven otherwise."""
    future = Provenance(
        producer="test",
        producer_version=1,
        resolution_method="quantum_resolved",
        analyzer_version="0.0.0",
    )
    with pytest.raises(SchemaError, match="cannot carry"):
        a_node(provenance=future, confidence=1.0)


def test_uncertainty_records_a_kind_not_merely_a_gap() -> None:
    node = a_node(
        provenance=GUESSED,
        confidence=0.0,
        uncertainty=Uncertainty(UncertaintyKind.NOT_EXAMINED, detail="size_limit_2MB"),
    )
    assert node.envelope()["uncertainty"] == {
        "kind": "not_examined",
        "detail": "size_limit_2MB",
    }


# -- ownership -------------------------------------------------------------


def test_one_owner_cannot_redefine_another_owners_node() -> None:
    graph = Graph()
    graph.add(a_node(owner="builtin"))
    with pytest.raises(OwnershipError, match="belongs to builtin"):
        graph.add(a_node(owner="plugin:thing", value={"key": "lr", "scalar": 0.9}))


def test_the_same_fact_inserted_twice_is_free() -> None:
    graph = Graph()
    first = graph.add(a_node())
    second = graph.add(a_node())
    assert first is second
    assert len(graph) == 1


def test_one_owner_producing_two_facts_for_one_identity_is_an_error() -> None:
    graph = Graph()
    graph.add(a_node())
    with pytest.raises(NodeConflictError, match="two different facts"):
        graph.add(a_node(value={"key": "lr", "scalar": 0.9}))


@pytest.mark.parametrize("owner", ["", "plugin:", "Plugin:Thing!", "someone"])
def test_an_unrecognised_owner_is_refused(owner: str) -> None:
    with pytest.raises(SchemaError, match="invalid owner"):
        a_node(owner=owner)


# -- the wire form ---------------------------------------------------------


def test_vocabulary_members_serialize_as_their_value() -> None:
    """``str()`` over a ``(str, Enum)`` mixin is not stable across versions."""
    assert enum_value(NodeType.CONFIGURATION_VALUE) == "ConfigurationValue"
    assert enum_value(EdgeType.PRODUCED_BY) == "PRODUCED_BY"
    assert enum_value(ResolutionMethod.MODEL_RANKED) == "model_ranked"
    assert json.loads(canonical_json(a_node().envelope()))["type"] == "ConfigurationValue"


def test_canonical_json_refuses_a_non_finite_number() -> None:
    with pytest.raises(SchemaError, match="non-finite"):
        a_node(value={"key": "lr", "scalar": float("nan")})


def test_a_value_the_wire_form_cannot_hold_is_refused_at_construction() -> None:
    with pytest.raises(SchemaError, match="unrepresentable"):
        a_node(value={"key": object()})


# -- the store -------------------------------------------------------------


def a_graph() -> Graph:
    graph = Graph()
    graph.add(a_node("configvalue:a.yaml#lr"))
    graph.add(a_node("configvalue:a.yaml#wd", value={"key": "wd", "scalar": 0.01}))
    graph.add_edge(
        Edge(
            source="configvalue:a.yaml#lr",
            type=EdgeType.REPORTED_IN,
            target="configsnapshot:a.yaml",
            provenance=PARSED,
        )
    )
    return graph


def test_a_written_graph_reads_back_byte_for_byte(tmp_path: Path) -> None:
    graph = a_graph()
    directory = write_graph(graph, tmp_path)
    reloaded = read_graph(directory)
    assert reloaded is not None
    assert reloaded.nodes == graph.nodes
    assert reloaded.edges == graph.edges
    assert render_nodes(reloaded) == render_nodes(graph)
    assert reloaded.content_id == graph.content_id


def test_writing_the_same_graph_again_changes_nothing(tmp_path: Path) -> None:
    graph = a_graph()
    directory = write_graph(graph, tmp_path)
    before = (directory / NODES_NAME).read_bytes()
    assert write_graph(graph, tmp_path) == directory
    assert (directory / NODES_NAME).read_bytes() == before


def test_no_graph_stored_is_not_an_error(tmp_path: Path) -> None:
    assert read_graph(tmp_path / "absent") is None


def test_an_unknown_node_type_is_kept_and_ignored(tmp_path: Path) -> None:
    """A plugin's node type outlives the build that does not know it."""
    payload = a_node().envelope()
    payload["type"] = "SomethingLater"
    payload.pop("content_id")
    node = node_from_envelope(payload)
    assert node.type == "SomethingLater"
    graph = Graph()
    graph.add(node)
    assert graph.of_type("SomethingLater") == (node,)
    assert graph.of_type(NodeType.CONFIGURATION_VALUE) == ()


def test_a_graph_from_a_future_major_is_refused(tmp_path: Path) -> None:
    directory = write_graph(a_graph(), tmp_path)
    header = json.loads((directory / HEADER_NAME).read_text())
    header["aeg_schema_version"] = AEG_SCHEMA_VERSION + 1
    (directory / HEADER_NAME).write_text(canonical_json(header) + "\n")
    with pytest.raises(UnsupportedSchemaError, match="this build reads"):
        read_graph(directory)


def test_a_node_that_does_not_hash_to_its_recorded_id_is_refused() -> None:
    """Checked per node, so a single edited entry is caught where it sits.

    Tested through ``node_from_envelope`` rather than through a written graph:
    the header digest would also catch this, and a test that cannot tell the
    two guards apart is evidence for neither.
    """
    payload = a_node().envelope()
    payload["value"] = {"key": "lr", "scalar": 99.9}
    with pytest.raises(SchemaError, match="does not hash to its recorded content id"):
        node_from_envelope(payload)


def test_a_tampered_node_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    """The repository owns this directory, so nothing in it is taken on faith."""
    directory = write_graph(a_graph(), tmp_path)
    lines = (directory / NODES_NAME).read_text().splitlines()
    first = json.loads(lines[0])
    first["value"]["scalar"] = 99.9
    lines[0] = canonical_json(first)
    (directory / NODES_NAME).write_text("\n".join(lines) + "\n")
    with pytest.raises(SchemaError, match="does not hash to its recorded content id"):
        read_graph(directory)


def test_a_header_that_disagrees_with_its_contents_is_refused(tmp_path: Path) -> None:
    """The second guard: entries each intact, but not the set that was written."""
    directory = write_graph(a_graph(), tmp_path)
    lines = (directory / NODES_NAME).read_text().splitlines()
    (directory / NODES_NAME).write_text(lines[0] + "\n")
    with pytest.raises(SchemaError, match="do not match the recorded header digest"):
        read_graph(directory)


def test_a_line_that_is_not_json_is_refused(tmp_path: Path) -> None:
    directory = write_graph(a_graph(), tmp_path)
    (directory / NODES_NAME).write_text("{not json}\n")
    with pytest.raises(SchemaError, match="is not JSON"):
        read_graph(directory)


# -- sharing ---------------------------------------------------------------


def test_nodes_citing_one_file_share_one_locator() -> None:
    """Without pooling, a repository's nodes each carry their own copy."""
    graph = Graph()
    first = graph.add(a_node("configvalue:a.yaml#lr"))
    second = graph.add(a_node("configvalue:a.yaml#wd", value={"key": "wd", "scalar": 0.5}))
    assert first.locations is second.locations
    assert first.provenance is second.provenance


# -- read tracking ---------------------------------------------------------


def test_a_query_records_what_it_touched() -> None:
    graph = a_graph()
    with graph.record_reads() as touched:
        graph.of_type(NodeType.CONFIGURATION_VALUE)
    assert touched == {"configvalue:a.yaml#lr", "configvalue:a.yaml#wd"}


def test_an_inner_blocks_reads_count_for_the_outer_one() -> None:
    graph = a_graph()
    with graph.record_reads() as outer:
        with graph.record_reads() as inner:
            graph.node("configvalue:a.yaml#lr")
        graph.node("configvalue:a.yaml#wd")
    assert inner == {"configvalue:a.yaml#lr"}
    assert outer == {"configvalue:a.yaml#lr", "configvalue:a.yaml#wd"}


# -- the config producer ---------------------------------------------------


def a_repository(tmp_path: Path, text: str, name: str = "conf/config.yaml") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return tmp_path


def built_graph(root: Path) -> Graph:
    return produce(collect(scan_repository(root)))


def test_a_parsed_scalar_is_a_fact(tmp_path: Path) -> None:
    graph = built_graph(a_repository(tmp_path, "optim:\n  lr: 0.0003\n"))
    (value,) = graph.of_type(NodeType.CONFIGURATION_VALUE)
    assert value.logical_id == "configvalue:conf/config.yaml#optim.lr"
    assert value.value == {"key": "optim.lr", "scalar": 0.0003, "canonical_name": "learning_rate"}
    assert value.confidence == 1.0
    assert value.provenance.resolution_method is ResolutionMethod.DIRECT_PARSE


def test_a_configuration_system_inferred_from_a_directory_name_is_not(tmp_path: Path) -> None:
    """§9: a filename heuristic may not be presented as semantic support."""
    graph = built_graph(a_repository(tmp_path, "defaults:\n  - model: base\nlr: 0.1\n"))
    (dependency,) = graph.of_type(NodeType.DEPENDENCY_DECLARATION)
    assert dependency.logical_id == "dependency:hydra-core"
    assert dependency.confidence == LEXICAL_CONFIDENCE
    assert dependency.confidence < 1.0
    assert dependency.provenance.resolution_method is ResolutionMethod.LEXICAL_MATCH
    (edge,) = graph.edges_from("configsnapshot:conf/config.yaml", EdgeType.DEPENDS_ON)
    assert edge.target == "dependency:hydra-core"
    assert edge.confidence < 1.0


def test_two_files_implying_one_dependency_do_not_conflict(tmp_path: Path) -> None:
    root = a_repository(tmp_path, "defaults:\n  - a: b\n", "conf/one.yaml")
    a_repository(root, "defaults:\n  - c: d\n", "conf/two.yaml")
    graph = built_graph(root)
    assert len(graph.of_type(NodeType.DEPENDENCY_DECLARATION)) == 1
    assert len(graph.edges_to("dependency:hydra-core", EdgeType.DEPENDS_ON)) == 2


def test_no_builtin_logical_id_encodes_a_line_number(tmp_path: Path) -> None:
    """§5.3: identity must survive a reformat, so it cannot name a line."""
    graph = built_graph(a_repository(tmp_path, "optim:\n  lr: 0.1\n  wd: 0.2\nseed: 42\n"))
    assert graph.nodes
    for node in graph.nodes:
        for location in node.locations:
            if location.line is not None:
                assert str(location.line) not in node.logical_id.rsplit(":", 1)[-1]


def test_a_repository_with_no_configuration_yields_an_empty_graph(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("nothing here\n")
    graph = built_graph(tmp_path)
    assert len(graph) == 0
    assert graph.content_id == Graph().content_id


# -- the command -----------------------------------------------------------

runner = CliRunner(env={"COLUMNS": "300"})


def test_the_graph_command_summarises(tmp_path: Path) -> None:
    root = a_repository(tmp_path, "defaults:\n  - a: b\noptim:\n  lr: 0.1\n")
    result = runner.invoke(app, ["graph", str(root)])
    assert result.exit_code == 0
    assert "ConfigurationValue" in plain(result.stdout)


def test_the_graph_command_renders_canonical_json(tmp_path: Path) -> None:
    root = a_repository(tmp_path, "optim:\n  lr: 0.1\n")
    result = runner.invoke(app, ["graph", str(root), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["aeg_schema_version"] == AEG_SCHEMA_VERSION
    assert payload["nodes"][0]["provenance"]["resolution_method"] == "direct_parse"
    assert result.stdout.strip() == canonical_json(payload)


def test_the_graph_command_refuses_an_unknown_format(tmp_path: Path) -> None:
    result = runner.invoke(app, ["graph", str(tmp_path), "--format", "yaml"])
    assert result.exit_code == 2


def test_the_graph_command_writes_a_store_only_when_asked(tmp_path: Path) -> None:
    root = a_repository(tmp_path, "optim:\n  lr: 0.1\n")
    assert runner.invoke(app, ["graph", str(root)]).exit_code == 0
    assert not (root / ".adduce" / "aeg").exists()
    assert runner.invoke(app, ["graph", str(root), "--store"]).exit_code == 0
    written = list((root / ".adduce" / "aeg").iterdir())
    assert len(written) == 1
    assert (written[0] / NODES_NAME).is_file()
