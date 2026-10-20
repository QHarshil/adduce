"""Static analysis of Python sources.

The collector resolves every call site to a fully qualified name through an
import-alias map (``import torch as th`` → ``th.manual_seed`` resolves to
``torch.manual_seed``) and applies one hop of wrapper resolution: when a
project-local function's body contains seeding primitives, calls to that
function count as calls to those primitives. That covers the dominant
research-code pattern of a central ``set_seed``/``seed_everything`` helper.

``calls`` answers whether a name is called anywhere, including in a helper
nothing invokes. The ``referenced`` queries answer the narrower question the
determinism rules need. Outside the tests, a site at module scope counts. A
site inside a function counts when project code names that function outside
its own body (a call, an import, a bare or attribute use), when a decorator
receives it, when it sits in a class named or decorated that way, or when its
module is imported or runs as an entrypoint. Project code here is every
non-test module, the code cells of non-test notebooks, and extensionless
Python scripts. Packaging metadata that declares a console script or other
entry point imports its module, and a dotted path in a config file (Hydra
``_target_``, LightningCLI ``class_path``, a gin binding) names its last
segment. A site in a test counts only when a non-test module imports that test
module. Names are matched, never resolved. This is deliberately generous: a
missed method call through ``self`` or a framework hook would turn real
seeding into a false "not seeded", which is worse than trusting a helper that
is merely named somewhere.

The ceiling is stated in the docs: ``getattr``, dynamic imports, and deeper
indirection cannot be resolved statically. Rules therefore express
confidence, never certainty.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from ..model import FileEntry, Repo
from .environment import _ENTRYPOINT_NAMES

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_SUPPRESS_RE = re.compile(r"#\s*adduce:\s*ignore\s*=\s*([A-Z0-9,\-\s]+)")
_MAIN_GUARD_RE = re.compile(r"__name__\s*==\s*[\"']__main__[\"']")
#: The same guard written the other way round. Kept out of ``has_main_guard``,
#: which R-EXEC-001 reads.
_REVERSED_MAIN_GUARD_RE = re.compile(r"[\"']__main__[\"']\s*==\s*__name__")
_IDENTIFIER_RE = re.compile(r"[^\W\d]\w*")
#: Longer than a name anyone types. A base64 payload splits into runs of
#: identifier characters, and without a cap each run would become a name.
_MAX_IDENTIFIER = 64
#: A larger config or extensionless file is data. The portability scan stops
#: at the same size, so no file is read for this collector alone.
_MAX_TEXT_BYTES = 2_000_000
_PYTHON_SHEBANG_RE = re.compile(r"#![^\n]*python")

#: A file under one of these directories is a test. A helper only a test calls
#: does not seed an experiment, so a test's references give no credit.
_TEST_DIRS = frozenset({"tests", "test"})
#: Only with a test framework import: research repositories name evaluation
#: scripts ``test_net.py``, and treating one as a test would drop real seeding.
_TEST_FILE_RE = re.compile(r"test_\w*\.py|\w+_test\.py")
_TEST_FRAMEWORKS = frozenset({"pytest", "unittest"})

#: Packaging metadata whose entry points name project code for a launcher.
_PACKAGING_FILES = frozenset({"pyproject.toml", "setup.cfg"})
#: ``module.path:attr.path``, the object reference of an entry point.
_ENTRY_TARGET_RE = re.compile(r"([^\W\d][\w.]*)\s*:\s*([^\W\d][\w.]*)")
#: ``name = module:attr`` at a line start (setup.cfg, an ini string in
#: setup.py) or just inside a quote (a list entry in setup.py). setup.cfg may
#: put the group's only entry on the group's own line,
#: ``console_scripts = name = module:attr``.
_ENTRY_SPEC_RE = re.compile(
    r"""(?:^|["'])[ \t]*(?:[\w.-]+[ \t]*=[ \t]*)?[\w.-]+[ \t]*=[ \t]*"""
    r"([^\W\d][\w.]*)[ \t]*:[ \t]*([^\W\d][\w.]*)",
    re.MULTILINE,
)
_SETUP_CFG_ENTRY_POINTS_RE = re.compile(
    r"^\[options\.entry_points\][^\n]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL
)
#: ``scripts=[...]`` in setup.py: files pip installs to run as commands.
_SETUP_SCRIPTS_RE = re.compile(r"\bscripts\s*=\s*[\[(]([^\])]*)")
_QUOTED_RE = re.compile(r"[\"']([^\"'\n]+)[\"']")

#: Config files that can name project code for a framework to construct.
_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".toml", ".gin"})
#: Hydra ``_target_`` and LightningCLI ``class_path``, in YAML, JSON or TOML.
_CONFIG_TARGET_RE = re.compile(
    r"""(?<![\w.])["']?(?:_target_|class_path)["']?[ \t]*[:=][ \t]*["']?([^\W\d][\w.]*)"""
)
#: A gin binding ``scope/module.fn.param = value`` configures ``fn``.
_GIN_BINDING_RE = re.compile(r"^[ \t]*(?:[\w.]+/)*([^\W\d][\w.]*)\.[^\W\d]\w*[ \t]*=", re.MULTILINE)
#: A gin reference ``@scope/module.fn`` or ``@fn()`` passes ``fn``.
_GIN_REFERENCE_RE = re.compile(r"@(?:[\w.]+/)*([^\W\d][\w.]*)")


#: Keyword arguments whose *values* are worth capturing (revision pins,
#: precision flags). Kept to an allowlist so CallSite stays lightweight.
_CAPTURED_KWARGS = frozenset(
    {
        "revision",
        "dtype",
        "torch_dtype",
        "precision",
        "fp16",
        "bf16",
        "tf32",
        "variant",
        "default",
        "mode",
        "deterministic",
        "repo_type",
        "workers",
    }
)


class Scope(NamedTuple):
    """One definition enclosing a site: a function, or a class when ``is_class``.

    ``decorated`` is set when a decorator receives the definition. A decorator
    can register it (``@cli.command()``, ``@register_model("x")``) for a
    dispatcher to call by a name the source never spells out.
    """

    name: str
    is_class: bool = False
    decorated: bool = False


@dataclass(frozen=True)
class CallSite:
    """A resolved call: fully qualified name plus location and keywords.

    ``kw_values`` holds the source-level value for a small allowlist of
    keywords (revision pins, precision flags) when the value is a literal or
    a resolvable dotted name; unresolvable values are absent. ``scope`` holds
    the enclosing functions and classes, outermost first; empty at module
    scope.
    """

    qualname: str
    file: str
    line: int
    keywords: frozenset[str] = frozenset()
    kw_values: tuple[tuple[str, str], ...] = ()
    first_arg: str | None = None  # first positional arg when a string literal
    first_literal: object = None
    scope: tuple[Scope, ...] = ()

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
    scope: tuple[Scope, ...] = ()


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
    #: Runs as a program that may call a function it never names: a
    #: ``__main__`` guard either way round, or a call to ``fire.Fire``, which
    #: puts every function in the module on the command line.
    is_launcher: bool = False
    #: Set when the module could not be turned into reliable evidence, whether
    #: because it failed to parse or because traversal aborted partway through
    #: an oversized tree. Either way the fields above hold their empty
    #: defaults; the AEG marks the module's source-file node ``parse_failed``
    #: rather than dropping it, and other consumers see an evidence-free
    #: record rather than an absent one.
    parse_error: bool = False
    #: Physical lines in this module. Zero when the module did not parse or
    #: the walk aborted partway through, so a sum over modules measures
    #: source actually analysed rather than present.
    line_count: int = 0
    #: Every name used outside a function or class of the same name: bare
    #: names, attribute names and imported names. When the module could not
    #: be analysed, every identifier in its text short enough to be a name,
    #: since the only call to a helper may sit in the part that was not read.
    references: set[str] = field(default_factory=set)
    #: Dotted module names this module imports, with each prefix. A relative
    #: import contributes the part after its dots. Every identifier in the
    #: text when the module could not be analysed, for the same reason.
    imported_modules: set[str] = field(default_factory=set)


#: Node types whose subtree can never hold anything this visitor extracts.
#: ``Name`` carries an identifier and an expression context, ``Constant`` a
#: literal and its kind, ``alias`` two identifiers — none of them can enclose an
#: import, a definition, a call or an assignment. Skipping them also skips the
#: contexts beneath them, which are the most common node in real source by a
#: wide margin. ``tests/test_python_ast_traversal.py`` re-derives this set from
#: parsed source on every supported Python version rather than trusting it.
_ELIDED_NODES = frozenset({ast.Name, ast.Constant, ast.alias})


class _FunctionExit(ast.AST):
    """Marker that unwinds the enclosing-function stack after a function body.

    The traversal is iterative, so leaving a subtree is not a return from a
    call. Pushing this before a function's children makes the exit the last
    thing popped for that function, which is what the recursive form got from
    the call stack.
    """

    _fields = ()


_FUNCTION_EXIT = _FunctionExit()


class _ClassExit(ast.AST):
    """The same marker for a class body, which encloses its methods."""

    _fields = ()


_CLASS_EXIT = _ClassExit()

#: ``cls._fields`` reversed, cached per node class. The worklist is a stack, so
#: children are pushed back to front to be popped in source order.
_REVERSED_FIELDS: dict[type, tuple[str, ...]] = {}


class _ModuleVisitor:
    """Single-pass visitor building the alias map and extracting call evidence.

    The alias map is flat (module- and function-level names share one
    namespace). Shadowing an import with a local variable of the same name is
    rare enough in research code that per-scope tracking is not worth the
    fragility.

    The walk is an explicit worklist rather than ``ast.NodeVisitor`` — see
    :meth:`visit`. Handlers extract only; descent belongs to the loop, so none
    of them recurses.
    """

    def __init__(self, path: str, module_name: str) -> None:
        self.analysis = ModuleAnalysis(path=path, module_name=module_name)
        self.aliases: dict[str, str] = {}
        # Functions only: each one collects the calls in its body for wrapper
        # resolution, which a class must not join.
        self._current_function: list[str] = []
        self._scope: list[Scope] = []
        # Functions and classes: a use of one of these names inside its own
        # body is recursion or self-construction, not a reference from outside.
        self._enclosing: list[str] = []
        # Module-level Name -> dict string-keys, for torch.save(ckpt, ...)
        # where ckpt was assembled a few lines earlier.
        self._dict_vars: dict[str, tuple[str, ...]] = {}
        self._stack: list[ast.AST] = []
        # Dispatch on the node class itself. ``ast.NodeVisitor`` formats a
        # method name per node and looks it up; at this volume that alone is
        # millions of string builds.
        self._dispatch: dict[type, Callable[[Any], None]] = {
            ast.Import: self.visit_Import,
            ast.ImportFrom: self.visit_ImportFrom,
            ast.FunctionDef: self.visit_FunctionDef,
            ast.AsyncFunctionDef: self.visit_AsyncFunctionDef,
            ast.Call: self.visit_Call,
            ast.Assign: self.visit_Assign,
            ast.ClassDef: self.visit_ClassDef,
            ast.Attribute: self.visit_Attribute,
            _FunctionExit: self._exit_function,
            _ClassExit: self._exit_class,
        }

    # -- traversal ----------------------------------------------------------

    def visit(self, root: ast.AST) -> None:
        """Walk ``root`` in source order, dispatching to the handlers below.

        ``ast.NodeVisitor`` costs a recursive call, a generator and a formatted
        attribute lookup for every node, and it descends into every expression
        context and literal even though no handler asks about one. Over the
        4,643 modules of the largest corpus repository this walk reaches 2.85M
        nodes against 8.35M, which is 2.7x less traversal and, paired against
        the recursive form on the same machine, 11.3% off a cold run.

        Field order matches ``generic_visit``: a decorator is still read after
        the body it decorates, so an enclosing-function attribution that held
        before holds now.
        """
        get_handler = self._dispatch.get
        stack = self._stack
        stack.append(root)
        pop = stack.pop
        push = stack.append
        elided = _ELIDED_NODES
        reversed_fields = _REVERSED_FIELDS
        name_node = ast.Name
        # Mutated in place by the definition handlers, so this alias stays current.
        enclosing = self._enclosing
        note = self.analysis.references.add
        while stack:
            node = pop()
            cls = node.__class__
            handler = get_handler(cls)
            if handler is not None:
                handler(node)
            fields = reversed_fields.get(cls)
            if fields is None:
                fields = reversed_fields[cls] = tuple(reversed(cls._fields))
            for name in fields:
                value: Any = getattr(node, name, None)
                if value.__class__ is list:
                    for item in reversed(value):
                        item_cls = item.__class__
                        # A Name is never pushed, so its identifier is read
                        # here. A use inside the definition of that name is
                        # not a reference from outside.
                        if item_cls is name_node:
                            if item.id not in enclosing:
                                note(item.id)
                        # A node with no fields has no children and no handler,
                        # so visiting it could not do anything.
                        elif item_cls not in elided and isinstance(item, ast.AST) and item._fields:
                            push(item)
                else:
                    value_cls = value.__class__
                    if value_cls is name_node:
                        if value.id not in enclosing:
                            note(value.id)
                    elif value_cls not in elided and isinstance(value, ast.AST) and value._fields:
                        push(value)

    # -- imports ----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self.aliases[bound] = target
            self.analysis.imports.add(alias.name.split(".")[0])
            self._note_module(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            root = node.module.split(".")[0]
            self.analysis.imports.add(root)
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                self.aliases[bound] = f"{node.module}.{alias.name}"
        # Relative imports resolve no alias, but they still import a project
        # module and name what they import from it.
        if node.module:
            self._note_module(node.module)
        for alias in node.names:
            if alias.name == "*":
                continue
            self.analysis.references.add(alias.name)
            # ``from pkg import seeding`` may import a submodule, not a name.
            self._note_module(f"{node.module}.{alias.name}" if node.module else alias.name)

    def _note_module(self, dotted: str) -> None:
        parts = dotted.split(".")
        for end in range(1, len(parts) + 1):
            self.analysis.imported_modules.add(".".join(parts[:end]))

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
        self._scope.append(Scope(node.name, decorated=bool(node.decorator_list)))
        self._enclosing.append(node.name)
        self.analysis.functions.setdefault(node.name, set())
        # Pushed before this node's children, so it is popped after all of
        # them — including nested functions, which unwind in the same order.
        self._stack.append(_FUNCTION_EXIT)

    def _exit_function(self, node: _FunctionExit) -> None:
        self._current_function.pop()
        self._scope.pop()
        self._enclosing.pop()

    def _exit_class(self, node: _ClassExit) -> None:
        self._scope.pop()
        self._enclosing.pop()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``self.seed_all()`` and ``worker_init_fn=utils.seed_worker`` name a
        # helper through an attribute static resolution cannot type.
        if node.attr not in self._enclosing:
            self.analysis.references.add(node.attr)

    # -- calls ---------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        qualname = self._resolve(node.func)
        if qualname:
            keywords = frozenset(kw.arg for kw in node.keywords if kw.arg)
            first_arg = None
            first_literal: object = None
            if node.args and isinstance(node.args[0], ast.Constant):
                first_literal = node.args[0].value
                if isinstance(first_literal, str):
                    first_arg = first_literal
            site = CallSite(
                qualname=qualname,
                file=self.analysis.path,
                line=node.lineno,
                keywords=keywords,
                kw_values=self._capture_kw_values(node),
                first_arg=first_arg,
                first_literal=first_literal,
                scope=tuple(self._scope),
            )
            self.analysis.calls.append(site)
            for fn in self._current_function:
                self.analysis.functions[fn].add(qualname)
            self._check_dataloader(node, qualname, keywords)
            self._check_env_call(node, qualname)
            self._check_torch_save(node, qualname)
            self._check_add_argument(node, qualname)

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

    # -- dataclass config defaults ------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(Scope(node.name, is_class=True, decorated=bool(node.decorator_list)))
        self._enclosing.append(node.name)
        self._stack.append(_CLASS_EXIT)
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
                    AssignSite(
                        target=resolved,
                        value=literal,
                        file=self.analysis.path,
                        line=line,
                        scope=tuple(self._scope),
                    )
                )


def _module_name_for(path: PurePosixPath) -> str:
    """Dotted module name for a repo-relative path, stripping src/ prefixes."""
    parts = list(path.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse_suppressions(source: str) -> dict[int, set[str]]:
    suppressions: dict[int, set[str]] = {}
    # split("\n"), not splitlines(): splitlines() also breaks on \f, \v,
    # \x1c-\x1e, \x85, U+2028 and U+2029, none of which the CPython tokenizer
    # treats as a line break. A file containing one would number these lines
    # differently from the lines a finding is anchored to, which moves a
    # suppression pragma onto some other finding and can put a reported line
    # past the end of the file.
    for lineno, line in enumerate(source.split("\n"), start=1):
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
    #: Non-module sources that name project code: notebooks, and the entry
    #: points packaging metadata declares. Each holds only ``references``,
    #: ``imported_modules`` and calls, and never joins ``modules``.
    referrers: list[ModuleAnalysis] = field(default_factory=list)
    #: Modules whose every function counts: imported by project code or a
    #: launcher, or an entrypoint in their own right.
    _live_modules: frozenset[str] = frozenset()
    #: Names that project code uses outside a definition of that name.
    _referenced_names: frozenset[str] = frozenset()
    _test_files: frozenset[str] = frozenset()

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

    # -- referenced code (see the module docstring) -------------------------

    def unreferenced_function(self, site: CallSite | AssignSite) -> str | None:
        """The definition keeping ``site`` from counting, or None if it counts.

        The empty string stands for the module scope of a test, which has no
        definition to name.
        """
        if site.file in self._live_modules:
            return None
        if site.file in self._test_files:
            # A test's own references were never collected, so a name match
            # here would come from unrelated project code.
            return site.scope[0].name if site.scope else ""
        unreferenced_class: str | None = None
        # Empty at module scope, which therefore always counts.
        for entry in site.scope:
            if entry.decorated or entry.name in self._referenced_names:
                if entry.is_class:
                    # A named or decorated class may be instantiated,
                    # subclassed or registered, any of which can run any of
                    # its methods.
                    return None
                continue
            if entry.is_class:
                # A method may still be reached by its own name, through an
                # instance obtained some way that does not name the class.
                unreferenced_class = unreferenced_class or entry.name
                continue
            return unreferenced_class or entry.name
        return None

    def in_referenced_code(self, site: CallSite | AssignSite) -> bool:
        return self.unreferenced_function(site) is None

    def is_test_file(self, path: str) -> bool:
        return path in self._test_files

    def referenced_call_sites(self, qualname: str) -> list[CallSite]:
        return [site for site in self.call_sites(qualname) if self.in_referenced_code(site)]

    def referenced_call_sites_terminal(self, terminal: str) -> list[CallSite]:
        return [site for site in self.call_sites_terminal(terminal) if self.in_referenced_code(site)]

    def calls_referenced(self, *qualnames: str) -> bool:
        """True when any of the names is called from code that counts."""
        return any(self.referenced_call_sites(qualname) for qualname in qualnames)

    def referenced_assign_sites(self, target: str) -> list[AssignSite]:
        return [site for site in self.assign_sites(target) if self.in_referenced_code(site)]

    def assigns_referenced(self, target: str, value: object) -> bool:
        return any(site.value == value for site in self.referenced_assign_sites(target))

    @property
    def main_guard_files(self) -> list[str]:
        return [m.path for m in self.modules if m.has_main_guard]

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


class PythonConsumer:
    """The per-file half of this collector, for the shared read pass.

    ``finish`` performs the cross-file indexing, which cannot run until every
    module has been fed.
    """

    def __init__(self) -> None:
        self.evidence = PythonEvidence()
        self.project_functions: dict[str, set[str]] = {}
        # Extensionless files without a Python shebang that parse as Python.
        # One counts only if setup.py lists it in ``scripts``, which may be
        # read after it.
        self._unmarked_scripts: dict[str, ModuleAnalysis] = {}
        self._listed_scripts: set[str] = set()

    def wants(self, entry: FileEntry) -> bool:
        if entry.suffix in {".py", ".ipynb"} or entry.name in _PACKAGING_FILES:
            return True
        return (
            (entry.suffix in _CONFIG_SUFFIXES or entry.suffix == "")
            and entry.size <= _MAX_TEXT_BYTES
            and not str(entry.path).startswith(".adduce/cache/")
        )

    def feed(self, entry: FileEntry, text: str) -> None:
        path = str(entry.path)
        if entry.suffix == ".ipynb":
            self.evidence.referrers.append(_analyse_notebook(path, text))
        elif entry.suffix == ".py":
            _analyse_module(self.evidence, self.project_functions, entry, text)
            if entry.name == "setup.py":
                _add_entry_points(self.evidence, path, _ENTRY_SPEC_RE.findall(text))
                self._listed_scripts |= _setup_py_scripts(entry.path, text)
        elif entry.suffix == "":
            self._feed_script(path, text)
        else:
            if entry.name in _PACKAGING_FILES:
                _add_entry_points(self.evidence, path, _packaging_entry_points(entry.name, text))
            if entry.suffix in _CONFIG_SUFFIXES:
                _add_config_references(self.evidence, path, entry.suffix, text)

    def _feed_script(self, path: str, text: str) -> None:
        if _PYTHON_SHEBANG_RE.match(text):
            self.evidence.referrers.append(_analyse_referrer(path, text))
            return
        # Most extensionless files are not Python at all: a licence, a
        # Makefile, a Dockerfile. Without a shebang, only one that parses can
        # be a script.
        try:
            ast.parse(text)
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            return
        self._unmarked_scripts[path] = _analyse_referrer(path, text)

    def finish(self) -> PythonEvidence:
        for path, script in self._unmarked_scripts.items():
            if path in self._listed_scripts:
                self.evidence.referrers.append(script)
        _index_evidence(self.evidence, self.project_functions)
        return self.evidence


def _identifiers(text: str) -> set[str]:
    """Every distinct identifier-shaped token in ``text`` short enough to be a name."""
    return {
        match.group()
        for match in _IDENTIFIER_RE.finditer(text)
        if match.end() - match.start() <= _MAX_IDENTIFIER
    }


def _record_unanalysable(evidence: PythonEvidence, rel: str, module_name: str, source: str) -> None:
    """Record a module that could not be safely turned into evidence.

    Used both when the source never became a tree and when a tree was built
    but the walk over it did not finish. Both cases get the same
    ``ModuleAnalysis``, empty except that every identifier in the text counts
    as a reference and an import: a hand-kept invariant that the two failure
    paths stay indistinguishable is not something a test can enforce, since
    the second path is unreachable by any known input, so the bookkeeping is
    centralised here instead of duplicated at each call site.
    """
    names = _identifiers(source)
    evidence.modules.append(
        ModuleAnalysis(
            path=rel,
            module_name=module_name,
            parse_error=True,
            references=names,
            imported_modules=set(names),
        )
    )


def _analyse_module(
    evidence: PythonEvidence,
    project_functions: dict[str, set[str]],
    entry: FileEntry,
    source: str,
) -> None:
    """Parse and analyse one module into the aggregate evidence."""
    rel = str(entry.path)
    visitor = _ModuleVisitor(path=rel, module_name=_module_name_for(entry.path))
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        # A pathologically deep or wide module can blow the parser stack
        # (MemoryError) or the compiler's recursive tree walk (RecursionError)
        # without being syntactically invalid. Treat it the same as a parse
        # failure: there is no tree to analyse.
        _record_unanalysable(evidence, rel, visitor.analysis.module_name, source)
        return
    try:
        visitor.visit(tree)
    except (MemoryError, RecursionError):
        # visit() is an explicit worklist, not recursion (see _ModuleVisitor
        # above), so nothing on this path recurses today. Guarded anyway: a
        # handler or an interpreter-level stack check could still raise here,
        # and a partially visited module must never be kept. A half-visited
        # module looks like complete evidence of absence — a rule could
        # conclude "no seed is set" when the seed call sat in the unvisited
        # subtree — which is a false positive, not a diagnostic gap.
        _record_unanalysable(evidence, rel, visitor.analysis.module_name, source)
        return
    analysis = visitor.analysis
    analysis.suppressions = _parse_suppressions(source)
    analysis.has_main_guard = bool(_MAIN_GUARD_RE.search(source))
    analysis.is_launcher = (
        analysis.has_main_guard
        or bool(_REVERSED_MAIN_GUARD_RE.search(source))
        or any(site.qualname == "fire.Fire" for site in analysis.calls)
    )
    analysis.line_count = source.count("\n") + (0 if source.endswith("\n") else 1)
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


def _strip_ipython(source: str) -> str:
    """Blank the magic and shell lines of a cell, which are not Python."""
    return "\n".join(
        "" if line.lstrip().startswith(("%", "!")) else line for line in source.split("\n")
    )


def _notebook_cells(raw: str) -> list[str] | None:
    """Code cell sources, or None when the text is not a notebook this can read."""
    try:
        data = json.loads(raw)
    except (ValueError, MemoryError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    cells = data.get("cells")
    if cells is None:
        # nbformat 3 kept its cells in worksheets and their code in ``input``.
        worksheets = data.get("worksheets")
        cells = [
            cell
            for sheet in (worksheets if isinstance(worksheets, list) else [])
            if isinstance(sheet, dict) and isinstance(sheet.get("cells"), list)
            for cell in sheet["cells"]
        ]
    if not isinstance(cells, list):
        return None
    sources: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", cell.get("input"))
        if isinstance(source, list):
            sources.append("".join(part for part in source if isinstance(part, str)))
        elif isinstance(source, str):
            sources.append(source)
    return sources


#: A JSON string literal, then the colon that makes it a key, if one follows.
_JSON_STRING_RE = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*")(\s*:)?')
_SOURCE_KEYS = frozenset({'"source"', '"input"'})


def _notebook_source_text(raw: str) -> str | None:
    """The code cell text of a notebook that does not load, or None when it has none.

    A merge conflict or a truncated write breaks the JSON but leaves its string
    literals whole. Each value belongs to the key before it, so outputs, which
    hold the base64 payloads, are skipped without following the nesting the
    damage broke.
    """
    key: str | None = None
    found = False
    parts: list[str] = []
    for match in _JSON_STRING_RE.finditer(raw):
        literal = match.group(1)
        if match.group(2):
            key = literal
            found = found or key in _SOURCE_KEYS
        elif key in _SOURCE_KEYS:
            try:
                parts.append(str(json.loads(literal)))
            except ValueError:
                parts.append(literal)
    return "\n".join(parts) if found else None


def _add_source(
    analysis: ModuleAnalysis, source: str, aliases: dict[str, str], *, ipython: bool = False
) -> None:
    """Add what one piece of Python source references, or its identifiers when it does not parse."""
    # A fresh visitor per piece: a walk that aborts leaves its stacks dirty.
    visitor = _ModuleVisitor(path=analysis.path, module_name="")
    visitor.aliases = aliases
    try:
        visitor.visit(ast.parse(_strip_ipython(source) if ipython else source))
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        names = _identifiers(source)
        analysis.references |= names
        analysis.imported_modules |= names
        return
    analysis.references |= visitor.analysis.references
    analysis.imported_modules |= visitor.analysis.imported_modules
    analysis.imports |= visitor.analysis.imports
    analysis.calls.extend(visitor.analysis.calls)


def _analyse_notebook(path: str, raw: str) -> ModuleAnalysis:
    """The references a notebook's code cells make to project code.

    A cell that does not parse contributes every identifier in it, and a
    notebook that does not load contributes every identifier in its cell
    sources, or in its text when none can be found, for the reason
    ``_record_unanalysable`` gives.
    """
    analysis = ModuleAnalysis(path=path, module_name="")
    cells = _notebook_cells(raw)
    if cells is None:
        source = _notebook_source_text(raw)
        names = _identifiers(raw if source is None else source)
        analysis.parse_error = True
        analysis.references = names
        analysis.imported_modules = set(names)
        return analysis
    # Cells share one namespace, so an import in one resolves a call in another.
    aliases: dict[str, str] = {}
    for source in cells:
        _add_source(analysis, source, aliases, ipython=True)
    return analysis


def _analyse_referrer(path: str, source: str) -> ModuleAnalysis:
    """The references a Python script that is not a module makes to project code."""
    analysis = ModuleAnalysis(path=path, module_name="")
    _add_source(analysis, source, {})
    return analysis


def _setup_py_scripts(setup: PurePosixPath, text: str) -> set[str]:
    """Repository paths of the files a setup.py lists in ``scripts``."""
    return {
        str(setup.parent / PurePosixPath(name))
        for block in _SETUP_SCRIPTS_RE.findall(text)
        for name in _QUOTED_RE.findall(block)
    }


def _add_config_references(evidence: PythonEvidence, path: str, suffix: str, text: str) -> None:
    """Record the objects a config file names for a framework to build or call."""
    dotted = _CONFIG_TARGET_RE.findall(text)
    if suffix == ".gin":
        dotted += _GIN_BINDING_RE.findall(text) + _GIN_REFERENCE_RE.findall(text)
    names = {name.rsplit(".", 1)[-1] for name in dotted}
    if names:
        referrer = ModuleAnalysis(path=path, module_name="")
        referrer.references = names
        evidence.referrers.append(referrer)


def _string_targets(value: object) -> Iterator[str]:
    """Entry point values in a TOML table: a string, or a Poetry inline table."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key in ("callable", "reference"):
            if isinstance(value.get(key), str):
                yield value[key]


def _table(parent: object, *keys: str) -> dict[str, Any]:
    """The table at ``keys``, or an empty one when any level is missing or not a table."""
    for key in keys:
        if not isinstance(parent, dict):
            return {}
        parent = parent.get(key)
    return parent if isinstance(parent, dict) else {}


def _packaging_entry_points(name: str, text: str) -> list[tuple[str, str]]:
    """``(module, attr)`` for each entry point a packaging file declares."""
    if name == "setup.cfg":
        section = _SETUP_CFG_ENTRY_POINTS_RE.search(text)
        return _ENTRY_SPEC_RE.findall(section.group(1)) if section else []
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, MemoryError, RecursionError):
        # pip cannot install from it either, so nothing it declares launches.
        return []
    tables = [
        _table(data, "project", "scripts"),
        _table(data, "project", "gui-scripts"),
        *_table(data, "project", "entry-points").values(),
        _table(data, "tool", "poetry", "scripts"),
        *_table(data, "tool", "poetry", "plugins").values(),
    ]
    targets: list[tuple[str, str]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        for value in table.values():
            for target in _string_targets(value):
                if match := _ENTRY_TARGET_RE.match(target.strip()):
                    targets.append((match.group(1), match.group(2)))
    return targets


def _add_entry_points(evidence: PythonEvidence, path: str, targets: list[tuple[str, str]]) -> None:
    """Record entry points as a referrer: the launcher imports the module and names the object."""
    if not targets:
        return
    referrer = ModuleAnalysis(path=path, module_name="")
    for module, attr in targets:
        parts = module.split(".")
        referrer.imported_modules.update(".".join(parts[:end]) for end in range(1, len(parts) + 1))
        referrer.references.update(attr.split("."))
    evidence.referrers.append(referrer)


def collect_python(repo: Repo) -> PythonEvidence:
    """Analyse all Python files and build the aggregated evidence.

    Walks and reads in one call, for a caller outside the shared read pass.
    """
    consumer = PythonConsumer()
    for entry in repo.files:
        if not consumer.wants(entry):
            continue
        source = repo.read_text(entry.path)
        if source is None:
            continue
        consumer.feed(entry, source)
    return consumer.finish()


def _index_evidence(
    evidence: PythonEvidence, project_functions: dict[str, set[str]]
) -> PythonEvidence:
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
    _index_references(evidence)
    return evidence


def _is_test_module(module: ModuleAnalysis) -> bool:
    path = PurePosixPath(module.path)
    if path.name == "conftest.py" or _TEST_DIRS.intersection(path.parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.fullmatch(path.name)) and bool(module.imports & _TEST_FRAMEWORKS)


def _is_imported(module: ModuleAnalysis, importers: dict[str, set[str]]) -> bool:
    # A leading ``src`` or ``lib`` is a layout directory in one repository and
    # an importable package in another. The trailing parts of the full path
    # tried here include the module name, which drops it.
    path = PurePosixPath(module.path).with_suffix("")
    parts = ".".join(path.parts[:-1] if path.name == "__init__" else path.parts).split(".")
    for start in range(len(parts)):
        if importers.get(".".join(parts[start:]), set()) - {module.path}:
            return True
    return False


def _is_entrypoint_file(path: str) -> bool:
    # The environment collector's entrypoint criterion. A script with one of
    # these names is run directly, often through a launcher that calls a
    # function the script never names.
    parts = PurePosixPath(path).parts
    return bool(parts) and parts[-1] in _ENTRYPOINT_NAMES and len(parts) <= 3


def _index_references(evidence: PythonEvidence) -> None:
    """Work out which modules and function names count as referenced.

    Only non-test modules and referrers give credit. An import matches a
    project module when it names the module's dotted path or a trailing part
    of it, because research scripts put their own directory on ``sys.path``
    and import siblings by bare name.
    """
    names: set[str] = set()
    importers: dict[str, set[str]] = {}
    live: set[str] = set()
    tests: set[str] = set()

    def credit(source: ModuleAnalysis) -> None:
        names.update(source.references)
        for dotted in source.imported_modules:
            importers.setdefault(dotted, set()).add(source.path)
        for site in source.calls:
            if site.qualname == "importlib.import_module" and site.first_arg:
                importers.setdefault(site.first_arg.lstrip("."), set()).add(source.path)

    for module in evidence.modules:
        if _is_test_module(module):
            tests.add(module.path)
            continue
        credit(module)
        if (
            module.is_launcher
            or PurePosixPath(module.path).name == "__main__.py"
            or _is_entrypoint_file(module.path)
            # torch.hub loads the functions of a top-level hubconf.py by name.
            or module.path == "hubconf.py"
        ):
            live.add(module.path)
    for referrer in evidence.referrers:
        if not _is_test_module(referrer):
            credit(referrer)
    for module in evidence.modules:
        if _is_imported(module, importers):
            live.add(module.path)
    evidence._live_modules = frozenset(live)
    evidence._referenced_names = frozenset(names)
    evidence._test_files = frozenset(tests)
