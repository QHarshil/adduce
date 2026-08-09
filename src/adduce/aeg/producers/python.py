"""Python nodes: the operations that decide whether a run repeats.

A call site is not a node. The collector indexes every call so that a rule can
ask about any qualified name, and almost none of that index is evidence about
anything: measured over the largest corpus repository, 7,657 of 386,146 call
sites are reachable through everything the 78 rules ask for, and the most
frequent names are ``super``, ``len`` and ``isinstance``. At the medium
stratum the reachable share is 0.34%. So the graph carries resolved facts —
a seeding call, a model load, a checkpoint write — and the general index stays
where it is, in the evidence layer, because ``ev.py.calls`` answers for names
no producer can enumerate in advance.

That makes this producer additive rather than a replacement, and its node
count is the honest cost of the graph rather than a saving.

How a name was resolved decides what its fact is worth. A fully qualified call
is ``ast_resolved``. A bare terminal match — ``model.half()``, or
``from_pretrained`` on a receiver static analysis cannot type — is
``lexical_match``, because the receiver is a guess. Neither may carry full
confidence, which is correct: both are inferences about what a name refers to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from ..graph import Graph
from ..nodes import Edge, Node, Provenance, SourceLocation, Uncertainty
from ..schema import EdgeType, NodeType, ResolutionMethod, UncertaintyKind
from . import analyzer_version

if TYPE_CHECKING:  # pragma: no cover
    from ...evidence import Evidence
    from ...evidence.python_ast import CallSite

#: Ordered, not calibrated. A resolved qualified name is stronger evidence than
#: a terminal match on an untyped receiver, and that ordering is the claim. The
#: magnitudes are provisional until the calibration table exists.
AST_CONFIDENCE = 0.9
LEXICAL_CONFIDENCE = 0.5

_SEED_FUNCTIONS = (
    "torch.manual_seed",
    "torch.cuda.manual_seed",
    "torch.cuda.manual_seed_all",
    "numpy.random.seed",
    "numpy.random.default_rng",
    "random.seed",
    "tensorflow.random.set_seed",
    "tf.random.set_seed",
    "keras.utils.set_random_seed",
    "jax.random.PRNGKey",
    "jax.random.key",
    "pytorch_lightning.seed_everything",
    "lightning.seed_everything",
    "lightning.pytorch.seed_everything",
    "lightning.fabric.seed_everything",
    "transformers.set_seed",
    "transformers.trainer_utils.set_seed",
    "accelerate.utils.set_seed",
)

_MODEL_LOADERS = ("torch.hub.load",)
_MODEL_TERMINALS = ("from_pretrained", "hf_hub_download", "snapshot_download", "SentenceTransformer")

_DATASET_LOADERS = ("datasets.load_dataset", "sklearn.datasets.fetch_openml")
_DATASET_TERMINALS = ("load_dataset",)

_EXECUTION_CALLS = (
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "os.system",
)

_PRECISION_CALLS = (
    "torch.autocast",
    "torch.amp.autocast",
    "torch.cuda.amp.autocast",
    "torch.set_float32_matmul_precision",
    "torch.use_deterministic_algorithms",
)
_PRECISION_TERMINALS = ("half", "bfloat16", "GradScaler")

_BACKEND_FLAGS = (
    "torch.backends.cudnn.deterministic",
    "torch.backends.cudnn.benchmark",
    "torch.backends.cuda.matmul.allow_tf32",
    "torch.backends.cudnn.allow_tf32",
)


def source_file_id(path: str) -> str:
    return f"sourcefile:{path}"


def operation_id(prefix: str, path: str, name: str, ordinal: int) -> str:
    """An identity that survives a reformat.

    The discriminator is the ordinal of this name within this file, never a
    line number: inserting a blank line moves every line below it, and an
    identity that moves with the text is not an identity.
    """
    return f"{prefix}:{path}#{name}#{ordinal}"


class PythonProducer:
    """Emits one node per source file and one per resolved operation."""

    name: str = "adduce.aeg.producers.python"
    version: int = 1
    produces: tuple[NodeType, ...] = (
        NodeType.SOURCE_FILE,
        NodeType.SEED_OPERATION,
        NodeType.MODEL_REFERENCE,
        NodeType.DATASET_REFERENCE,
        NodeType.CHECKPOINT_REFERENCE,
        NodeType.EXECUTION_COMMAND,
        NodeType.ENVIRONMENT_CONSTRAINT,
    )

    def run(self, graph: Graph, evidence: Evidence) -> None:
        version = analyzer_version()
        inventoried = graph.provenance(
            Provenance(
                producer=self.name,
                producer_version=self.version,
                resolution_method=ResolutionMethod.DIRECT_PARSE,
                analyzer_version=version,
                parser="ast.parse",
                parser_version="cpython",
            )
        )
        resolved = graph.provenance(
            Provenance(
                producer=self.name,
                producer_version=self.version,
                resolution_method=ResolutionMethod.AST_RESOLVED,
                analyzer_version=version,
                parser="ast.parse",
                parser_version="cpython",
            )
        )
        lexical = graph.provenance(
            Provenance(
                producer=self.name,
                producer_version=self.version,
                resolution_method=ResolutionMethod.LEXICAL_MATCH,
                analyzer_version=version,
                parser="ast.parse",
                parser_version="cpython",
            )
        )

        py = evidence.py
        for module in py.modules:
            graph.add(
                Node(
                    logical_id=source_file_id(module.path),
                    type=NodeType.SOURCE_FILE,
                    value={
                        "path": module.path,
                        "module": module.module_name,
                        "line_count": module.line_count,
                        "has_main_guard": module.has_main_guard,
                    },
                    provenance=inventoried,
                    locations=(SourceLocation(module.path),),
                    uncertainty=(
                        Uncertainty(UncertaintyKind.PARSE_FAILED)
                        if module.parse_error
                        else None
                    ),
                )
            )

        self._calls(graph, py, resolved, _SEED_FUNCTIONS, NodeType.SEED_OPERATION, "seedop")
        self._calls(graph, py, resolved, _MODEL_LOADERS, NodeType.MODEL_REFERENCE, "modelref")
        self._calls(graph, py, resolved, _DATASET_LOADERS, NodeType.DATASET_REFERENCE, "datasetref")
        self._calls(graph, py, resolved, _EXECUTION_CALLS, NodeType.EXECUTION_COMMAND, "exec")
        self._calls(
            graph, py, resolved, _PRECISION_CALLS, NodeType.ENVIRONMENT_CONSTRAINT, "precision"
        )
        self._terminals(
            graph, py, lexical, _MODEL_TERMINALS, NodeType.MODEL_REFERENCE, "modelref"
        )
        self._terminals(
            graph, py, lexical, _DATASET_TERMINALS, NodeType.DATASET_REFERENCE, "datasetref"
        )
        self._terminals(
            graph, py, lexical, _PRECISION_TERMINALS, NodeType.ENVIRONMENT_CONSTRAINT, "precision"
        )
        self._backend_flags(graph, py, resolved)
        self._checkpoints(graph, py, resolved)
        self._environment(graph, py, resolved)

    # -- emitters ----------------------------------------------------------

    def _calls(
        self,
        graph: Graph,
        py: Any,
        provenance: Provenance,
        names: Sequence[str],
        node_type: NodeType,
        prefix: str,
    ) -> None:
        for name in names:
            self._emit_sites(
                graph, py.call_sites(name), provenance, name, node_type, prefix, AST_CONFIDENCE
            )

    def _terminals(
        self,
        graph: Graph,
        py: Any,
        provenance: Provenance,
        names: Sequence[str],
        node_type: NodeType,
        prefix: str,
    ) -> None:
        for name in names:
            self._emit_sites(
                graph,
                py.call_sites_terminal(name),
                provenance,
                name,
                node_type,
                prefix,
                LEXICAL_CONFIDENCE,
            )

    def _emit_sites(
        self,
        graph: Graph,
        sites: Iterable[CallSite],
        provenance: Provenance,
        name: str,
        node_type: NodeType,
        prefix: str,
        confidence: float,
    ) -> None:
        ordinals: dict[str, int] = {}
        for site in sites:
            ordinal = ordinals.get(site.file, 0)
            ordinals[site.file] = ordinal + 1
            value: dict[str, Any] = {"call": name, "resolved_name": site.qualname}
            if site.first_arg is not None:
                value["first_argument"] = site.first_arg
            if site.keywords:
                value["keywords"] = sorted(site.keywords)
            self._add(
                graph,
                operation_id(prefix, site.file, name, ordinal),
                node_type,
                value,
                provenance,
                site.file,
                site.line,
                confidence,
            )

    def _backend_flags(self, graph: Graph, py: Any, provenance: Provenance) -> None:
        for flag in _BACKEND_FLAGS:
            ordinals: dict[str, int] = {}
            for site in py.assign_sites(flag):
                ordinal = ordinals.get(site.file, 0)
                ordinals[site.file] = ordinal + 1
                self._add(
                    graph,
                    operation_id("backendflag", site.file, flag, ordinal),
                    NodeType.ENVIRONMENT_CONSTRAINT,
                    {"setting": flag, "value": _scalar(site.value)},
                    provenance,
                    site.file,
                    site.line,
                    AST_CONFIDENCE,
                )

    def _checkpoints(self, graph: Graph, py: Any, provenance: Provenance) -> None:
        ordinals: dict[str, int] = {}
        for site in py.torch_saves:
            ordinal = ordinals.get(site.file, 0)
            ordinals[site.file] = ordinal + 1
            value: dict[str, Any] = {"saves_mapping": site.saves_dict}
            if site.dict_keys is not None:
                value["keys"] = sorted(site.dict_keys)
            self._add(
                graph,
                operation_id("checkpoint", site.file, "torch.save", ordinal),
                NodeType.CHECKPOINT_REFERENCE,
                value,
                provenance,
                site.file,
                site.line,
                AST_CONFIDENCE,
            )

    def _environment(self, graph: Graph, py: Any, provenance: Provenance) -> None:
        for module in py.modules:
            for name in sorted(module.env_sets):
                self._add(
                    graph,
                    operation_id("envset", module.path, name, 0),
                    NodeType.ENVIRONMENT_CONSTRAINT,
                    {"variable": name, "set_in_process": True},
                    provenance,
                    module.path,
                    None,
                    AST_CONFIDENCE,
                )

    def _add(
        self,
        graph: Graph,
        logical_id: str,
        node_type: NodeType,
        value: dict[str, Any],
        provenance: Provenance,
        path: str,
        line: int | None,
        confidence: float,
    ) -> None:
        graph.add(
            Node(
                logical_id=logical_id,
                type=node_type,
                value=value,
                provenance=provenance,
                locations=(SourceLocation(path, line),),
                confidence=confidence,
            )
        )
        graph.add_edge(
            Edge(
                source=logical_id,
                type=EdgeType.REPORTED_IN,
                target=source_file_id(path),
                provenance=provenance,
                confidence=confidence,
            )
        )


def _scalar(value: object) -> Any:
    """Keep a value the wire form can hold, and name what it cannot."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)
