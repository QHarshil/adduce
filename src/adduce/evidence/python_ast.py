"""Static analysis of Python sources.

The collector resolves every call site to a fully qualified name through an
import-alias map (``import torch as th`` → ``th.manual_seed`` resolves to
``torch.manual_seed``) and applies one hop of wrapper resolution: when a
project-local function's body contains seeding primitives, calls to that
function count as calls to those primitives. That covers the dominant
research-code pattern of a central ``set_seed``/``seed_everything`` helper.

The ceiling is stated in the docs: ``getattr``, dynamic imports, and deeper
indirection cannot be resolved statically. Rules therefore express
confidence, never certainty.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Repo

_SUPPRESS_RE = re.compile(r"#\s*adduce:\s*ignore\s*=\s*([A-Z0-9,\-\s]+)")
_MAIN_GUARD_RE = re.compile(r"__name__\s*==\s*[\"']__main__[\"']")


#: Keyword arguments whose *values* are worth capturing (revision pins,
#: precision flags). Kept to an allowlist so CallSite stays lightweight.
_CAPTURED_KWARGS = frozenset(
    {"revision", "dtype", "torch_dtype", "precision", "fp16", "bf16", "tf32", "variant", "default"}
)


@dataclass(frozen=True)
class CallSite:
    """A resolved call: fully qualified name plus location and keywords.

    ``kw_values`` holds the source-level value for a small allowlist of
    keywords (revision pins, precision flags) when the value is a literal or
    a resolvable dotted name; unresolvable values are absent.
    """

    qualname: str
    file: str
    line: int
    keywords: frozenset[str] = frozenset()
    kw_values: tuple[tuple[str, str], ...] = ()
    first_arg: str | None = None  # first positional arg when a string literal

    def kw_value(self, name: str) -> str | None:
        for key, value in self.kw_values:
            if key == name:
                return value
        return None


@dataclass(frozen=True)
class AssignSite:
    """An attribute assignment such as ``torch.backends.cudnn.deterministic = True``."""

    target: str
    value: object
    file: str
    line: int


@dataclass(frozen=True)
class DataLoaderSite:
    """A ``torch.utils.data.DataLoader(...)`` construction and its relevant kwargs.

    ``shuffle`` and ``num_workers`` are None when the value is not a literal
    (an expression or variable we cannot evaluate statically).
    """

    file: str
    line: int
    shuffle: bool | None
    num_workers: int | None
    has_generator: bool
    has_worker_init_fn: bool
    has_sampler: bool


@dataclass(frozen=True)
class EstimatorSite:
    """A call into an sklearn API that accepts ``random_state``."""

    qualname: str
    file: str
    line: int
    has_random_state: bool


@dataclass(frozen=True)
class TorchSaveSite:
    """A ``torch.save(...)`` call and, when statically visible, what it saves.

    ``dict_keys`` is the set of string keys in the saved dict literal (or a
    module-level variable assigned one); None when the payload shape cannot
    be seen statically. ``saves_dict`` distinguishes "saved a dict with
    unknown keys" from "saved a bare object (state_dict only)".
    """

    file: str
    line: int
    dict_keys: tuple[str, ...] | None
    saves_dict: bool


@dataclass(frozen=True)
class CliArg:
    """An ``argparse.add_argument`` (or dataclass field) with its default."""

    name: str
    default: object
    file: str
    line: int


#: sklearn callables that take random_state and where omitting it matters.
_SKLEARN_SEEDED = frozenset(
    {
        "train_test_split",
        "KFold",
        "StratifiedKFold",
        "GroupKFold",
        "ShuffleSplit",
        "StratifiedShuffleSplit",
        "GroupShuffleSplit",
        "RepeatedKFold",
        "RepeatedStratifiedKFold",
        "RandomizedSearchCV",
        "RandomForestClassifier",
        "RandomForestRegressor",
        "ExtraTreesClassifier",
        "ExtraTreesRegressor",
        "GradientBoostingClassifier",
        "GradientBoostingRegressor",
        "HistGradientBoostingClassifier",
        "HistGradientBoostingRegressor",
        "DecisionTreeClassifier",
        "DecisionTreeRegressor",
        "LogisticRegression",
        "SGDClassifier",
        "SGDRegressor",
        "MLPClassifier",
        "MLPRegressor",
        "KMeans",
        "MiniBatchKMeans",
        "GaussianMixture",
        "PCA",
        "TruncatedSVD",
        "TSNE",
        "resample",
        "make_classification",
        "make_regression",
        "make_blobs",
    }
)


@dataclass
class ModuleAnalysis:
    """Everything extracted from one Python module."""

    path: str
    module_name: str
    imports: set[str] = field(default_factory=set)
    calls: list[CallSite] = field(default_factory=list)
    assigns: list[AssignSite] = field(default_factory=list)
    env_sets: set[str] = field(default_factory=set)
    dataloaders: list[DataLoaderSite] = field(default_factory=list)
    functions: dict[str, set[str]] = field(default_factory=dict)
    suppressions: dict[int, set[str]] = field(default_factory=dict)
    torch_saves: list[TorchSaveSite] = field(default_factory=list)
    cli_args: list[CliArg] = field(default_factory=list)
    dataclass_defaults: list[CliArg] = field(default_factory=list)
    has_main_guard: bool = False
    parse_error: bool = False


class _ModuleVisitor(ast.NodeVisitor):
    """Single-pass visitor building the alias map and extracting call evidence.

    The alias map is flat (module- and function-level names share one
    namespace). Shadowing an import with a local variable of the same name is
    rare enough in research code that per-scope tracking is not worth the
    fragility.
    """

    def __init__(self, path: str, module_name: str) -> None:
        self.analysis = ModuleAnalysis(path=path, module_name=module_name)
        self.aliases: dict[str, str] = {}
        self._current_function: list[str] = []
        # Module-level Name -> dict string-keys, for torch.save(ckpt, ...)
        # where ckpt was assembled a few lines earlier.
        self._dict_vars: dict[str, tuple[str, ...]] = {}

    # -- imports ----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self.aliases[bound] = target
            self.analysis.imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            root = node.module.split(".")[0]
            self.analysis.imports.add(root)
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                self.aliases[bound] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    # -- resolution helpers -------------------------------------------------

    def _resolve(self, node: ast.expr) -> str | None:
        """Resolve a Name/Attribute chain to a dotted, alias-expanded name."""
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        parts.reverse()
        base = self.aliases.get(parts[0], parts[0])
        return ".".join([base, *parts[1:]])

    # -- functions (for wrapper resolution) ---------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._current_function.append(node.name)
        self.analysis.functions.setdefault(node.name, set())
        self.generic_visit(node)
        self._current_function.pop()

    # -- calls ---------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        qualname = self._resolve(node.func)
        if qualname:
            keywords = frozenset(kw.arg for kw in node.keywords if kw.arg)
            first_arg = None
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                first_arg = node.args[0].value
            site = CallSite(
                qualname=qualname,
                file=self.analysis.path,
                line=node.lineno,
                keywords=keywords,
                kw_values=self._capture_kw_values(node),
                first_arg=first_arg,
            )
            self.analysis.calls.append(site)
            for fn in self._current_function:
                self.analysis.functions[fn].add(qualname)
            self._check_dataloader(node, qualname, keywords)
            self._check_env_call(node, qualname)
            self._check_torch_save(node, qualname)
            self._check_add_argument(node, qualname)
        self.generic_visit(node)

    def _value_repr(self, node: ast.expr) -> str | None:
        """A source-level representation of a literal or dotted-name value."""
        if isinstance(node, ast.Constant):
            return repr(node.value) if isinstance(node.value, str) else str(node.value)
        resolved = self._resolve(node)
        return resolved

    def _capture_kw_values(self, node: ast.Call) -> tuple[tuple[str, str], ...]:
        captured: list[tuple[str, str]] = []
        for kw in node.keywords:
            if kw.arg in _CAPTURED_KWARGS:
                value = self._value_repr(kw.value)
                if value is not None:
                    captured.append((kw.arg, value))
        return tuple(captured)

    def _dict_string_keys(self, node: ast.expr) -> tuple[str, ...] | None:
        """String keys of a dict literal / dict(...) call / known dict variable."""
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            return tuple(keys)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            return tuple(kw.arg for kw in node.keywords if kw.arg)
        if isinstance(node, ast.Name):
            return self._dict_vars.get(node.id)
        return None

    def _check_torch_save(self, node: ast.Call, qualname: str) -> None:
        if qualname != "torch.save" or not node.args:
            return
        payload = node.args[0]
        keys = self._dict_string_keys(payload)
        saves_dict = keys is not None or isinstance(payload, ast.Dict)
        # A bare model.state_dict() first argument means weights-only.
        if keys is None and isinstance(payload, ast.Call):
            inner = self._resolve(payload.func) or ""
            if inner.endswith(".state_dict"):
                keys = ()
                saves_dict = False
        self.analysis.torch_saves.append(
            TorchSaveSite(
                file=self.analysis.path,
                line=node.lineno,
                dict_keys=keys,
                saves_dict=saves_dict,
            )
        )

    def _check_add_argument(self, node: ast.Call, qualname: str) -> None:
        if not qualname.endswith(".add_argument") or not node.args:
            return
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return
        name = first.value.lstrip("-").replace("-", "_")
        default: object = None
        for kw in node.keywords:
            if kw.arg == "default":
                try:
                    default = ast.literal_eval(kw.value)
                except (ValueError, SyntaxError):
                    default = self._resolve(kw.value)
        self.analysis.cli_args.append(
            CliArg(name=name, default=default, file=self.analysis.path, line=node.lineno)
        )

    def _check_dataloader(self, node: ast.Call, qualname: str, keywords: frozenset[str]) -> None:
        if not (qualname == "torch.utils.data.DataLoader" or qualname.endswith(".DataLoader") or qualname == "DataLoader"):
            return
        # Only count it when torch is plausibly the source, to avoid
        # flagging unrelated DataLoader classes.
        if "torch" not in qualname and "torch" not in self.analysis.imports:
            return
        shuffle: bool | None = None
        num_workers: int | None = None
        for kw in node.keywords:
            if kw.arg == "shuffle" and isinstance(kw.value, ast.Constant):
                shuffle = bool(kw.value.value)
            if kw.arg == "num_workers" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                num_workers = int(value) if isinstance(value, int) else None
        self.analysis.dataloaders.append(
            DataLoaderSite(
                file=self.analysis.path,
                line=node.lineno,
                shuffle=shuffle,
                num_workers=num_workers,
                has_generator="generator" in keywords,
                has_worker_init_fn="worker_init_fn" in keywords,
                has_sampler="sampler" in keywords or "batch_sampler" in keywords,
            )
        )

    def _check_env_call(self, node: ast.Call, qualname: str) -> None:
        if qualname in {"os.environ.setdefault", "os.putenv"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self.analysis.env_sets.add(first.value)

    # -- assignments -----------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assignment(target, node.value, node.lineno)
        # Track simple Name = {..} for later torch.save(ckpt, path) shape lookup.
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (keys := self._dict_string_keys(node.value)) is not None
        ):
            self._dict_vars[node.targets[0].id] = keys
        self.generic_visit(node)

    # -- dataclass config defaults ------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_dataclass = any(
            (self._resolve(d) or "").split(".")[-1] == "dataclass"
            or (isinstance(d, ast.Call) and (self._resolve(d.func) or "").split(".")[-1] == "dataclass")
            for d in node.decorator_list
        )
        if is_dataclass:
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.value is not None
                ):
                    try:
                        default: object = ast.literal_eval(stmt.value)
                    except (ValueError, SyntaxError):
                        continue
                    self.analysis.dataclass_defaults.append(
                        CliArg(
                            name=stmt.target.id,
                            default=default,
                            file=self.analysis.path,
                            line=stmt.lineno,
                        )
                    )
        self.generic_visit(node)

    def _record_assignment(self, target: ast.expr, value: ast.expr, line: int) -> None:
        # os.environ["NAME"] = ...
        if (
            isinstance(target, ast.Subscript)
            and (resolved := self._resolve(target.value)) == "os.environ"
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
        ):
            self.analysis.env_sets.add(target.slice.value)
            return
        if isinstance(target, ast.Attribute):
            resolved = self._resolve(target)
            if resolved:
                try:
                    literal: object = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    literal = None
                self.analysis.assigns.append(
                    AssignSite(target=resolved, value=literal, file=self.analysis.path, line=line)
                )


def _module_name_for(path: Path) -> str:
    """Dotted module name for a repo-relative path, stripping src/ prefixes."""
    parts = list(path.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse_suppressions(source: str) -> dict[int, set[str]]:
    suppressions: dict[int, set[str]] = {}
    for lineno, line in enumerate(source.splitlines(), start=1):
        match = _SUPPRESS_RE.search(line)
        if match:
            ids = {part.strip() for part in match.group(1).split(",") if part.strip()}
            if ids:
                suppressions[lineno] = ids
    return suppressions


@dataclass
class PythonEvidence:
    """Aggregated, wrapper-expanded view over all analysed modules."""

    modules: list[ModuleAnalysis] = field(default_factory=list)
    _call_index: dict[str, list[CallSite]] = field(default_factory=dict)
    _effective_names: set[str] = field(default_factory=set)
    _assign_index: dict[str, list[AssignSite]] = field(default_factory=dict)
    env_sets: set[str] = field(default_factory=set)
    dataloaders: list[DataLoaderSite] = field(default_factory=list)
    estimators: list[EstimatorSite] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)
    suppressions: dict[str, dict[int, set[str]]] = field(default_factory=dict)
    torch_saves: list[TorchSaveSite] = field(default_factory=list)
    cli_args: list[CliArg] = field(default_factory=list)
    dataclass_defaults: list[CliArg] = field(default_factory=list)
    _terminal_index: dict[str, list[CallSite]] = field(default_factory=dict)

    # -- query API used by rules ------------------------------------------

    def calls(self, qualname: str) -> bool:
        """True when the qualified name is called directly or via a one-hop wrapper."""
        return qualname in self._effective_names

    def calls_any(self, *qualnames: str) -> bool:
        return any(self.calls(q) for q in qualnames)

    def call_sites(self, qualname: str) -> list[CallSite]:
        return self._call_index.get(qualname, [])

    def call_sites_terminal(self, terminal: str) -> list[CallSite]:
        """All call sites whose final segment matches, regardless of receiver.

        Needed for method calls on values static analysis cannot type, such
        as ``model.half()`` or ``AutoModel.from_pretrained(...)``.
        """
        return self._terminal_index.get(terminal, [])

    def assigns(self, target: str, value: object) -> bool:
        return any(site.value == value for site in self._assign_index.get(target, []))

    def assign_sites(self, target: str) -> list[AssignSite]:
        return self._assign_index.get(target, [])

    def sets_env(self, name: str) -> bool:
        return name in self.env_sets

    @property
    def main_guard_files(self) -> list[str]:
        return [m.path for m in self.modules if m.has_main_guard]

    @property
    def uses_numpy_generator(self) -> bool:
        return self.calls("numpy.random.default_rng") or self.calls("numpy.random.Generator")

    def dataloader_gaps(self) -> list[DataLoaderSite]:
        """DataLoader sites missing a seeded RNG source they need.

        A shuffling loader without an explicit ``generator=`` draws its
        sample order from the global RNG; a multi-worker loader without
        ``worker_init_fn`` leaves worker-local numpy/random state unseeded.
        Sites where ``shuffle``/``num_workers`` could not be evaluated
        statically are not reported as gaps.
        """
        gaps = []
        for site in self.dataloaders:
            shuffle_gap = site.shuffle is True and not site.has_generator and not site.has_sampler
            worker_gap = (
                site.num_workers is not None
                and site.num_workers > 0
                and not site.has_worker_init_fn
            )
            if shuffle_gap or worker_gap:
                gaps.append(site)
        return gaps

    def unseeded_estimators(self) -> list[EstimatorSite]:
        return [e for e in self.estimators if not e.has_random_state]


def collect_python(repo: Repo) -> PythonEvidence:
    """Analyse all Python files and build the aggregated evidence."""
    evidence = PythonEvidence()
    project_functions: dict[str, set[str]] = {}

    for entry in repo.python_files():
        source = repo.read_text(entry.path)
        if source is None:
            continue
        rel = str(entry.path)
        visitor = _ModuleVisitor(path=rel, module_name=_module_name_for(entry.path))
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            evidence.modules.append(
                ModuleAnalysis(path=rel, module_name=visitor.analysis.module_name, parse_error=True)
            )
            continue
        visitor.visit(tree)
        analysis = visitor.analysis
        analysis.suppressions = _parse_suppressions(source)
        analysis.has_main_guard = bool(_MAIN_GUARD_RE.search(source))
        evidence.modules.append(analysis)

        evidence.imports |= analysis.imports
        evidence.env_sets |= analysis.env_sets
        evidence.dataloaders.extend(analysis.dataloaders)
        evidence.torch_saves.extend(analysis.torch_saves)
        evidence.cli_args.extend(analysis.cli_args)
        evidence.dataclass_defaults.extend(analysis.dataclass_defaults)
        if analysis.suppressions:
            evidence.suppressions[rel] = analysis.suppressions
        for name, called in analysis.functions.items():
            project_functions.setdefault(name, set()).update(called)

    # Index direct calls and assignments.
    for module in evidence.modules:
        for site in module.calls:
            evidence._call_index.setdefault(site.qualname, []).append(site)
            evidence._terminal_index.setdefault(site.qualname.rsplit(".", 1)[-1], []).append(site)
            terminal = site.qualname.rsplit(".", 1)[-1]
            if terminal in _SKLEARN_SEEDED and (
                site.qualname.startswith("sklearn.") or "sklearn" in module.imports
            ):
                evidence.estimators.append(
                    EstimatorSite(
                        qualname=site.qualname,
                        file=site.file,
                        line=site.line,
                        has_random_state="random_state" in site.keywords,
                    )
                )
        for assign in module.assigns:
            evidence._assign_index.setdefault(assign.target, []).append(assign)

    # Effective names: direct calls plus one hop through project wrappers.
    # A call is treated as a wrapper invocation when its terminal segment
    # names a project-local function and the call does not resolve into a
    # third-party package root. Imports of the project's own modules
    # (``from utils import set_seed``) must not count as third-party.
    project_roots = {m.module_name.split(".")[0] for m in evidence.modules if m.module_name}
    external_roots = set(evidence.imports) - project_roots
    effective = set(evidence._call_index)
    for qualname in list(evidence._call_index):
        terminal = qualname.rsplit(".", 1)[-1]
        root = qualname.split(".")[0]
        if terminal in project_functions and root not in external_roots:
            effective |= project_functions[terminal]
    evidence._effective_names = effective
    return evidence
