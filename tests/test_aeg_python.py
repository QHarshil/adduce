"""The Python producer: what becomes a fact, and what stays an index.

The load-bearing decision here is that a call site is not a node. Measured over
the largest corpus repository, 7,657 of 386,146 call sites are reachable
through everything the rule layer asks for, and the most frequent names are
``super``, ``len`` and ``isinstance``. So the graph carries resolved
operations and the general index stays in the evidence layer, where a rule can
still ask about a name no producer enumerated.

The other property worth guarding is that an identity survives a reformat.
Testing that by looking for a line number inside a string is not a test of it —
a path with a digit in it passes by accident. So it is tested behaviourally:
insert blank lines, and assert the identities do not move while the locations
do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adduce.aeg import Graph, NodeType, ResolutionMethod, UncertaintyKind, canonical_json
from adduce.aeg.producers import produce
from adduce.aeg.producers.python import (
    _SEED_FUNCTIONS,
    AST_CONFIDENCE,
    LEXICAL_CONFIDENCE,
    PythonProducer,
)
from adduce.evidence import collect
from adduce.model import scan_repository
from adduce.rules.determinism import _UMBRELLA_SEEDERS

SEEDED = """\
import random

import torch


def main():
    torch.manual_seed(0)
    random.seed(0)
    print(len([1, 2, 3]), isinstance(1, int), super)
"""


def build(root: Path) -> Graph:
    return produce(collect(scan_repository(root)))


def a_repo(tmp_path: Path, source: str, name: str = "train.py") -> Path:
    (tmp_path / name).write_text(source)
    return tmp_path


# -- what becomes a fact ---------------------------------------------------


def test_a_seeding_call_becomes_a_located_operation(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, SEEDED))
    seeds = graph.of_type(NodeType.SEED_OPERATION)
    assert {node.value["call"] for node in seeds} == {"torch.manual_seed", "random.seed"}
    (torch_seed,) = [n for n in seeds if n.value["call"] == "torch.manual_seed"]
    assert torch_seed.locations[0].path == "train.py"
    assert torch_seed.locations[0].line == 7


def test_a_resolved_call_is_an_inference_not_a_parsed_fact(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, SEEDED))
    (node,) = [n for n in graph.of_type(NodeType.SEED_OPERATION) if n.value["call"] == "random.seed"]
    assert node.provenance.resolution_method is ResolutionMethod.AST_RESOLVED
    assert node.confidence == AST_CONFIDENCE
    assert node.confidence < 1.0


def test_a_terminal_match_is_worth_less_than_a_resolved_one(tmp_path: Path) -> None:
    """``model.half()`` names a method on a receiver nothing can type."""
    graph = build(a_repo(tmp_path, "def f(model):\n    return model.half()\n"))
    (node,) = graph.of_type(NodeType.ENVIRONMENT_CONSTRAINT)
    assert node.provenance.resolution_method is ResolutionMethod.LEXICAL_MATCH
    assert node.confidence == LEXICAL_CONFIDENCE
    assert LEXICAL_CONFIDENCE < AST_CONFIDENCE


def test_an_ordinary_call_becomes_nothing(tmp_path: Path) -> None:
    """The index is not the evidence: most calls are evidence about nothing."""
    graph = build(a_repo(tmp_path, "def f(x):\n    return len(list(sorted(x)))\n"))
    assert graph.of_type(NodeType.SEED_OPERATION) == ()
    assert [n.type for n in graph.nodes] == [NodeType.SOURCE_FILE]


def test_every_source_file_is_a_node(tmp_path: Path) -> None:
    a_repo(tmp_path, SEEDED, "train.py")
    a_repo(tmp_path, "x = 1\n", "helper.py")
    graph = build(tmp_path)
    assert {n.value["path"] for n in graph.of_type(NodeType.SOURCE_FILE)} == {
        "train.py",
        "helper.py",
    }


def test_an_operation_points_at_the_file_that_holds_it(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, SEEDED))
    (node,) = [n for n in graph.of_type(NodeType.SEED_OPERATION) if n.value["call"] == "random.seed"]
    (edge,) = graph.edges_from(node.logical_id)
    assert edge.target == "sourcefile:train.py"


# -- silence with a shape --------------------------------------------------


def test_a_file_that_did_not_parse_says_so(tmp_path: Path) -> None:
    """A file that could not be read is not the same as a file with nothing in it."""
    graph = build(a_repo(tmp_path, "def broken(:\n"))
    (node,) = graph.of_type(NodeType.SOURCE_FILE)
    assert node.uncertainty is not None
    assert node.uncertainty.kind is UncertaintyKind.PARSE_FAILED


def test_a_file_that_parsed_carries_no_uncertainty(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, "x = 1\n"))
    (node,) = graph.of_type(NodeType.SOURCE_FILE)
    assert node.uncertainty is None


# -- identity --------------------------------------------------------------


def test_an_identity_survives_a_reformat(tmp_path: Path) -> None:
    """Lines move under formatting; identities must not."""
    before = build(a_repo(tmp_path, SEEDED))
    reformatted = "\n\n\n" + SEEDED.replace("def main():", "def main():\n\n")
    after = build(a_repo(tmp_path, reformatted))

    assert [n.logical_id for n in before.nodes] == [n.logical_id for n in after.nodes]
    lines_before = [n.locations[0].line for n in before.of_type(NodeType.SEED_OPERATION)]
    lines_after = [n.locations[0].line for n in after.of_type(NodeType.SEED_OPERATION)]
    assert lines_before != lines_after


def test_two_calls_to_one_function_in_one_file_are_distinct(tmp_path: Path) -> None:
    graph = build(
        a_repo(tmp_path, "import torch\ntorch.manual_seed(0)\ntorch.manual_seed(1)\n")
    )
    seeds = graph.of_type(NodeType.SEED_OPERATION)
    assert len(seeds) == 2
    assert len({n.logical_id for n in seeds}) == 2


def test_the_same_call_in_two_files_does_not_collide(tmp_path: Path) -> None:
    a_repo(tmp_path, "import torch\ntorch.manual_seed(0)\n", "a.py")
    a_repo(tmp_path, "import torch\ntorch.manual_seed(0)\n", "b.py")
    graph = build(tmp_path)
    assert len(graph.of_type(NodeType.SEED_OPERATION)) == 2


# -- the producer and the rule layer must not drift ------------------------


def test_the_producer_covers_every_seeder_the_rules_ask_about() -> None:
    """Two tables naming the same thing drift; this is what catches it."""
    assert set(_UMBRELLA_SEEDERS) <= set(_SEED_FUNCTIONS)


def test_the_producer_declares_what_it_emits(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, SEEDED))
    emitted = {n.type for n in graph.nodes if n.provenance.producer == PythonProducer.name}
    assert emitted <= set(PythonProducer.produces)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import subprocess\nsubprocess.run(['ls'])\n", NodeType.EXECUTION_COMMAND),
        ("import torch\ntorch.save({}, 'ckpt.pt')\n", NodeType.CHECKPOINT_REFERENCE),
        ("import datasets\ndatasets.load_dataset('x')\n", NodeType.DATASET_REFERENCE),
        ("import torch\ntorch.hub.load('a', 'b')\n", NodeType.MODEL_REFERENCE),
        (
            "import torch\ntorch.backends.cudnn.deterministic = True\n",
            NodeType.ENVIRONMENT_CONSTRAINT,
        ),
        ("import os\nos.environ['PYTHONHASHSEED'] = '0'\n", NodeType.ENVIRONMENT_CONSTRAINT),
    ],
)
def test_each_supported_operation_reaches_the_graph(
    tmp_path: Path, source: str, expected: NodeType
) -> None:
    graph = build(a_repo(tmp_path, source))
    assert expected in {n.type for n in graph.nodes}


# -- secrets are never echoed ----------------------------------------------

#: Assembled at import rather than written out, so no line of this file is
#: itself a credential shape for adduce's own scan to report.
A_TOKEN = "hf_" + "A1b2C3d4E5f6" * 3


def test_a_first_argument_that_carries_a_secret_is_withheld(tmp_path: Path) -> None:
    """A shell command literal is repository bytes copied into a node."""
    source = f"import subprocess\nsubprocess.run('huggingface-cli login {A_TOKEN}')\n"
    graph = build(a_repo(tmp_path, source))
    (node,) = graph.of_type(NodeType.EXECUTION_COMMAND)
    assert node.value == {
        "call": "subprocess.run",
        "redacted": "first_argument",
        "resolved_name": "subprocess.run",
        "secret_kind": "Hugging Face token",
    }
    assert A_TOKEN not in canonical_json(node.envelope())


def test_an_ordinary_first_argument_is_kept(tmp_path: Path) -> None:
    graph = build(a_repo(tmp_path, "import subprocess\nsubprocess.run('ls -l')\n"))
    (node,) = graph.of_type(NodeType.EXECUTION_COMMAND)
    assert node.value["first_argument"] == "ls -l"
    assert "redacted" not in node.value
