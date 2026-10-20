"""Name resolution, wrapper expansion, and structural extraction from Python sources."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Callable

import pytest


def test_direct_call_resolution(make_evidence):
    ev = make_evidence({"train.py": "import torch\ntorch.manual_seed(0)\n"})
    assert ev.py.calls("torch.manual_seed")
    sites = ev.py.call_sites("torch.manual_seed")
    assert sites[0].file == "train.py" and sites[0].line == 2


def test_import_alias_resolution(make_evidence):
    ev = make_evidence({"train.py": "import torch as th\nth.manual_seed(0)\nth.cuda.manual_seed_all(0)\n"})
    assert ev.py.calls("torch.manual_seed")
    assert ev.py.calls("torch.cuda.manual_seed_all")


def test_from_import_resolution(make_evidence):
    ev = make_evidence({"train.py": "from torch import manual_seed as ms\nms(0)\n"})
    assert ev.py.calls("torch.manual_seed")


def test_from_import_submodule(make_evidence):
    ev = make_evidence(
        {"train.py": "from torch.backends import cudnn\ncudnn.benchmark = False\n"}
    )
    assert ev.py.assigns("torch.backends.cudnn.benchmark", False)


def test_numpy_conventional_alias(make_evidence):
    ev = make_evidence({"train.py": "import numpy as np\nnp.random.seed(42)\n"})
    assert ev.py.calls("numpy.random.seed")


def test_one_hop_wrapper_same_file(make_evidence):
    source = (
        "import random\nimport numpy as np\nimport torch\n\n"
        "def set_seed(seed):\n"
        "    random.seed(seed)\n"
        "    np.random.seed(seed)\n"
        "    torch.manual_seed(seed)\n\n"
        "set_seed(0)\n"
    )
    ev = make_evidence({"train.py": source})
    assert ev.py.calls("torch.manual_seed")
    assert ev.py.calls("numpy.random.seed")
    assert ev.py.calls("random.seed")


def test_one_hop_wrapper_cross_module(make_evidence):
    ev = make_evidence(
        {
            "utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n",
            "train.py": "from utils import seed_everything\nseed_everything(0)\n",
        }
    )
    assert ev.py.calls("torch.manual_seed")


def test_one_hop_wrapper_via_module_attribute(make_evidence):
    ev = make_evidence(
        {
            "utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n",
            "train.py": "import utils\nutils.seed_everything(0)\n",
        }
    )
    assert ev.py.calls("torch.manual_seed")


def test_call_inside_uninvoked_helper_still_counts(make_evidence):
    ev = make_evidence(
        {"utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n"}
    )
    # Deliberate: ``calls`` answers for a primitive appearing anywhere, even
    # inside a helper never invoked in the scanned tree, because rules outside
    # determinism ask whether a name occurs at all. The determinism rules ask
    # the narrower referenced query, which a helper nothing names fails.
    assert ev.py.calls("torch.manual_seed")
    assert not ev.py.calls_referenced("torch.manual_seed")


def test_env_assignment_and_setdefault(make_evidence):
    source = (
        "import os\n"
        "os.environ['PYTHONHASHSEED'] = '0'\n"
        "os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')\n"
    )
    ev = make_evidence({"a.py": source})
    assert ev.py.sets_env("PYTHONHASHSEED")
    assert ev.py.sets_env("CUBLAS_WORKSPACE_CONFIG")


def test_dataloader_extraction(make_evidence):
    source = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "g = torch.Generator()\n"
        "good = DataLoader(ds, shuffle=True, generator=g, num_workers=4, worker_init_fn=fn)\n"
        "bad = DataLoader(ds, shuffle=True, num_workers=2)\n"
        "cpu_only = DataLoader(ds, shuffle=False)\n"
    )
    ev = make_evidence({"data.py": source})
    assert len(ev.py.dataloaders) == 3
    gaps = ev.py.dataloader_gaps()
    assert len(gaps) == 1
    assert gaps[0].line == 5


def test_dataloader_with_sampler_is_not_a_shuffle_gap(make_evidence):
    source = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(ds, sampler=sampler)\n"
    )
    ev = make_evidence({"data.py": source})
    assert ev.py.dataloader_gaps() == []


def test_non_torch_dataloader_ignored(make_evidence):
    ev = make_evidence({"a.py": "from mylib import DataLoader\nDataLoader(x, shuffle=True)\n"})
    assert ev.py.dataloaders == []


def test_sklearn_random_state_detection(make_evidence):
    source = (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import train_test_split\n"
        "clf = RandomForestClassifier(n_estimators=100, random_state=0)\n"
        "train_test_split(X, y)\n"
    )
    ev = make_evidence({"model.py": source})
    assert len(ev.py.estimators) == 2
    unseeded = ev.py.unseeded_estimators()
    assert len(unseeded) == 1
    assert unseeded[0].qualname.endswith("train_test_split")


def test_inline_suppression_parsing(make_evidence):
    ev = make_evidence(
        {"a.py": "import torch\nx = 1  # adduce: ignore=R-DET-001, R-DET-002\n"}
    )
    assert ev.py.suppressions["a.py"][2] == {"R-DET-001", "R-DET-002"}


def test_syntax_error_does_not_crash(make_evidence):
    ev = make_evidence({"broken.py": "def f(:\n", "ok.py": "import torch\ntorch.manual_seed(0)\n"})
    assert any(m.parse_error for m in ev.py.modules)
    assert ev.py.calls("torch.manual_seed")


def _elif_chain_source(branches: int) -> str:
    lines = ["def f(x):", "    if x == 0:", "        pass"]
    for i in range(1, branches):
        lines.append(f"    elif x == {i}:")
        lines.append("        pass")
    return "\n".join(lines) + "\n"


def _binop_chain_source(terms: int) -> str:
    return "x = " + " + ".join("1" for _ in range(terms)) + "\n"


def _unary_chain_source(depth: int) -> str:
    return "x = " + "-" * depth + "1\n"


# A minimal, out-of-process probe for how a candidate source behaves. Reads
# the source from stdin so the caller never has to shell-quote megabytes of
# generated code.
_OVERFLOW_PROBE = (
    "import ast, sys\n"
    "try:\n"
    "    ast.parse(sys.stdin.read())\n"
    "except (MemoryError, RecursionError):\n"
    "    print('raised')\n"
    "else:\n"
    "    print('ok')\n"
)


def _overflow_outcome(source: str) -> str:
    """Return 'raised', 'ok', or 'crashed' for how a fresh interpreter handles ``ast.parse(source)``.

    Run out of process. The whole point of this probe is to find inputs that
    make ast.parse misbehave, and on some interpreter and platform
    combinations that misbehavior is an uncaught native stack overflow
    (the process is killed by a signal) rather than a Python exception --
    which would take this test process down with it if attempted in-process.
    """
    result = subprocess.run(
        [sys.executable, "-c", _OVERFLOW_PROBE],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode < 0:
        return "crashed"
    return "raised" if result.stdout.strip() == "raised" else "ok"


def _overflow_source(build: Callable[[int], str], start: int, limit: int) -> str | None:
    """The smallest ``build(n)`` for ``n`` doubling from ``start`` to ``limit``
    whose ``ast.parse`` raises ``(MemoryError, RecursionError)`` in a fresh
    interpreter, or ``None`` if growth reaches a native crash, or ``limit``,
    before any size raises cleanly.
    """
    n = start
    while n <= limit:
        outcome = _overflow_outcome(build(n))
        if outcome == "raised":
            return build(n)
        if outcome == "crashed":
            return None
        n *= 2
    return None


def _compiler_overflow_source() -> str:
    """A benign module whose parse overflows ast.parse's AST-construction
    step, distinctly from the elif chain's parser-stack overflow above.

    A long chain of binary additions is the shape that does this: pegen
    parses it iteratively (left-recursion is memoised, not deeply recursed),
    so the overflow is deferred to the later step that recursively builds
    nested BinOp nodes -- a RecursionError from CPython 3.11 on. On CPython
    3.10 that construction step has no recursion guard at all, so growing
    this shape does not raise; it corrupts the native stack outright and the
    interpreter is killed. Where that happens, fall back to a chain of
    unary negations, which is parsed by genuine recursive descent (no
    left-recursion optimisation applies) and so overflows the same guarded
    parser-stack limit as the elif chain, just through a different shape.
    """
    source = _overflow_source(_binop_chain_source, start=1_000, limit=2_000_000)
    if source is not None:
        return source
    source = _overflow_source(_unary_chain_source, start=1_000, limit=200_000)
    if source is None:
        raise AssertionError("no fixture overflowed ast.parse without crashing on this interpreter")
    return source


def _elif_overflow_source() -> str:
    """An elif chain sized to overflow ast.parse's pegen parser-stack limit.

    MAXSTACK is a compile-time constant, but not the same constant on every
    CPython build, so the branch count that overflows it is interpreter- and
    platform-dependent. Sizing the fixture at test time keeps this
    regression non-vacuous everywhere instead of pinning a number measured
    on one interpreter.
    """
    source = _overflow_source(_elif_chain_source, start=1_000, limit=500_000)
    if source is None:
        raise AssertionError("no elif chain up to 500000 branches overflowed ast.parse here")
    return source


def test_parser_stack_overflow_does_not_crash(make_evidence):
    # See _elif_overflow_source: ast.parse itself raises MemoryError or
    # RecursionError here, not SyntaxError or ValueError.
    source = _elif_overflow_source()
    with pytest.raises((MemoryError, RecursionError)):
        ast.parse(source)
    ev = make_evidence({"huge.py": source, "ok.py": "import torch\ntorch.manual_seed(0)\n"})
    modules = {m.path: m for m in ev.py.modules}
    assert modules["huge.py"].parse_error
    assert ev.py.calls("torch.manual_seed")


def test_compiler_recursion_overflow_does_not_crash(make_evidence):
    # See _compiler_overflow_source: ast.parse itself raises MemoryError or
    # RecursionError here, not SyntaxError or ValueError. Sizing (and, on
    # interpreters that cannot raise cleanly for the preferred shape,
    # reshaping) the fixture out of process keeps this non-vacuous without
    # risking this process on a native crash.
    source = _compiler_overflow_source()
    with pytest.raises((MemoryError, RecursionError)):
        ast.parse(source)
    ev = make_evidence({"huge.py": source, "ok.py": "import torch\ntorch.manual_seed(0)\n"})
    modules = {m.path: m for m in ev.py.modules}
    assert modules["huge.py"].parse_error
    assert ev.py.calls("torch.manual_seed")


def test_main_guard_detection(make_evidence):
    ev = make_evidence({"cli.py": "if __name__ == '__main__':\n    pass\n"})
    assert ev.py.main_guard_files == ["cli.py"]


# -- referenced code -------------------------------------------------------
#
# A seeding site inside a function counts only when something outside the
# function names it, or its module is imported or runs as an entrypoint. Each
# test below isolates one way of being referenced, or one way of not being.

_HELPER = "import numpy as np\n\ndef seed_all(seed):\n    np.random.seed(seed)\n"


def _seeded(ev) -> bool:
    return ev.py.calls_referenced("numpy.random.seed")


def test_module_scope_seeding_counts_in_a_module_nothing_imports(make_evidence):
    ev = make_evidence({"seeding.py": "import numpy as np\nnp.random.seed(0)\n"})
    assert _seeded(ev)


def test_helper_nothing_names_does_not_count(make_evidence):
    ev = make_evidence({"seeding.py": _HELPER, "train.py": "import numpy as np\nx = np.zeros(3)\n"})
    site = ev.py.call_sites("numpy.random.seed")[0]
    assert ev.py.unreferenced_function(site) == "seed_all"
    assert not _seeded(ev)


@pytest.mark.parametrize(
    "train",
    [
        pytest.param("from seeding import seed_all\nseed_all(0)\n", id="call"),
        pytest.param("import seeding\n", id="module-import"),
        pytest.param("from pkg import seeding\n", id="from-import-of-a-submodule"),
        pytest.param("from pkg.seeding import *\n", id="star-import"),
        pytest.param("import importlib\nimportlib.import_module('seeding')\n", id="import-module"),
        pytest.param("class Run:\n    def go(self, helpers):\n        helpers.seed_all(0)\n", id="attribute"),
        pytest.param("CALLBACKS = [seed_all]\n", id="bare-name-in-a-list"),
        pytest.param("HOOK = seed_all\n", id="bare-name"),
    ],
)
def test_each_kind_of_reference_counts(make_evidence, train):
    ev = make_evidence({"pkg/seeding.py": _HELPER, "train.py": train})
    assert _seeded(ev)


def test_a_lib_package_is_matched_by_its_full_path(make_evidence):
    # The module name drops a leading ``lib/`` as a source layout directory.
    ev = make_evidence({"lib/seeding.py": _HELPER, "train.py": "from lib import seeding\n"})
    assert _seeded(ev)


def test_a_relative_import_of_the_module_counts(make_evidence):
    ev = make_evidence(
        {
            "pkg/__init__.py": "",
            "pkg/seeding.py": _HELPER + "OTHER = 1\n",
            "pkg/train.py": "from .seeding import OTHER\n",
        }
    )
    assert _seeded(ev)


def test_importing_the_name_alone_counts(make_evidence):
    # ``from . import seed_all`` imports no module called seed_all, so only the
    # imported name can give credit here.
    ev = make_evidence({"pkg/__init__.py": _HELPER, "pkg/train.py": "from . import seed_all\n"})
    assert _seeded(ev)


def test_a_sibling_imported_by_bare_name_counts(make_evidence):
    ev = make_evidence({"scripts/seeding.py": _HELPER, "scripts/train.py": "import seeding\n"})
    assert _seeded(ev)


def test_an_import_from_another_package_does_not_match_by_last_name(make_evidence):
    ev = make_evidence({"seeding.py": _HELPER, "train.py": "from vendor import seeding\n"})
    assert not _seeded(ev)


def test_a_module_importing_its_namesake_is_not_imported_by_itself(make_evidence):
    ev = make_evidence({"utils.py": "X = 1\n", "lib/utils.py": "import utils\n" + _HELPER})
    assert not _seeded(ev)


_UNNAMED_MAIN = "import numpy as np\n\ndef main(_):\n    np.random.seed(0)\n\n"


@pytest.mark.parametrize(
    "path, source",
    [
        # ``tools/finetune.py`` is not an entrypoint name, so only how it
        # launches can make ``main`` count.
        pytest.param(
            "tools/finetune.py", _UNNAMED_MAIN + "if __name__ == '__main__':\n    run()\n", id="main-guard"
        ),
        pytest.param(
            "tools/finetune.py",
            _UNNAMED_MAIN + "if '__main__' == __name__:\n    run()\n",
            id="reversed-main-guard",
        ),
        pytest.param("tools/finetune.py", "import fire\n" + _UNNAMED_MAIN + "fire.Fire()\n", id="fire"),
        pytest.param(
            "tools/finetune.py", "from fire import Fire\n" + _UNNAMED_MAIN + "Fire(run)\n", id="fire-with-an-argument"
        ),
        pytest.param(
            "tools/finetune.py",
            "import fire\n" + _UNNAMED_MAIN + "def cli():\n    fire.Fire()\n\ncli()\n",
            id="fire-inside-a-function",
        ),
        pytest.param("pkg/__main__.py", _UNNAMED_MAIN, id="dunder-main"),
        pytest.param("hubconf.py", _UNNAMED_MAIN, id="hubconf"),
    ],
)
def test_functions_in_an_entrypoint_module_count(make_evidence, path, source):
    # ``tf.app.run()``, ``fire.Fire()`` and torch.hub call functions the module
    # never names.
    ev = make_evidence({path: source})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "path, source",
    [
        pytest.param("tools/finetune.py", "import fire\n" + _UNNAMED_MAIN, id="imports-fire-only"),
        pytest.param("tools/finetune.py", "import campfire\n" + _UNNAMED_MAIN + "campfire.Fire()\n", id="other-fire"),
        pytest.param("models/hubconf.py", _UNNAMED_MAIN, id="nested-hubconf"),
    ],
)
def test_a_module_that_does_not_launch_its_functions_gives_them_no_credit(make_evidence, path, source):
    # Control: green before launchers were widened. Pins that only a call to
    # fire.Fire, and only a top-level hubconf.py, counts.
    ev = make_evidence({path: source})
    assert not _seeded(ev)


@pytest.mark.parametrize(
    "use",
    [
        pytest.param("seed_all(seed, depth - 1)", id="call"),
        pytest.param("register([seed_all])", id="in-a-list"),
    ],
)
def test_recursion_is_not_a_reference(make_evidence, use):
    source = (
        "import numpy as np\n\n"
        "def seed_all(seed, depth=0):\n"
        "    np.random.seed(seed)\n"
        "    if depth:\n"
        f"        {use}\n"
    )
    ev = make_evidence({"seeding.py": source})
    assert not _seeded(ev)


def test_recursion_through_an_attribute_is_not_a_reference(make_evidence):
    source = (
        "import numpy as np\n\n"
        "class Seeder:\n"
        "    def seed_all(self, seed):\n"
        "        np.random.seed(seed)\n"
        "        self.seed_all(seed)\n"
    )
    ev = make_evidence({"seeding.py": source})
    assert not _seeded(ev)


def test_a_nested_helper_counts_only_when_every_enclosing_function_is_named(make_evidence):
    nested = (
        "import numpy as np\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        np.random.seed(0)\n"
        "    return inner\n"
    )
    assert not _seeded(make_evidence({"seeding.py": nested}))
    assert _seeded(make_evidence({"seeding.py": nested + "\nouter()\n"}))


def test_a_nested_helper_its_outer_function_never_names_does_not_count(make_evidence):
    nested = (
        "import numpy as np\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        np.random.seed(0)\n"
        "    return 1\n\n"
        "outer()\n"
    )
    ev = make_evidence({"seeding.py": nested})
    assert ev.py.unreferenced_function(ev.py.call_sites("numpy.random.seed")[0]) == "inner"
    assert not _seeded(ev)


def test_a_helper_called_from_a_method_of_an_instantiated_class_counts(make_evidence):
    ev = make_evidence(
        {
            "seeding.py": _HELPER,
            "trainer.py": (
                "from seeding import seed_all\n\n"
                "class Trainer:\n"
                "    def __init__(self):\n"
                "        seed_all(0)\n"
            ),
            "train.py": "from trainer import Trainer\n\nif __name__ == '__main__':\n    Trainer()\n",
        }
    )
    assert _seeded(ev)


@pytest.mark.parametrize(
    "path, source",
    [
        pytest.param("tests/test_seed.py", "from seeding import seed_all\nseed_all(0)\n", id="tests-dir"),
        pytest.param("test/check_seed.py", "from seeding import seed_all\nseed_all(0)\n", id="test-dir"),
        pytest.param("conftest.py", "from seeding import seed_all\nseed_all(0)\n", id="conftest"),
        pytest.param(
            "test_seed.py", "import pytest\nfrom seeding import seed_all\nseed_all(0)\n", id="test-prefix"
        ),
        pytest.param(
            "seed_test.py", "import unittest\nfrom seeding import seed_all\nseed_all(0)\n", id="test-suffix"
        ),
    ],
)
def test_a_reference_from_a_test_does_not_count(make_evidence, path, source):
    ev = make_evidence({"seeding.py": _HELPER, path: source})
    assert not _seeded(ev)


def test_a_test_with_a_main_guard_is_not_an_entrypoint(make_evidence):
    source = (
        "import unittest\nimport numpy as np\n\n"
        "def seed_all():\n    np.random.seed(0)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    ev = make_evidence({"tests/test_seed.py": source})
    assert not _seeded(ev)


def test_an_evaluation_script_named_like_a_test_still_gives_credit(make_evidence):
    # ``tools/test_net.py`` evaluates a model in detection repositories. Without
    # a test framework import it is not a test.
    ev = make_evidence({"seeding.py": _HELPER, "tools/test_net.py": "from seeding import seed_all\nseed_all(0)\n"})
    assert _seeded(ev)


def test_a_module_that_did_not_parse_may_still_hold_the_reference(make_evidence):
    ev = make_evidence({"seeding.py": _HELPER, "train.py": "print 'py2'\nseed_all(0)\n"})
    assert any(module.parse_error for module in ev.py.modules)
    assert _seeded(ev)


def test_referenced_assignments_follow_the_same_rule(make_evidence):
    helper = "import torch\n\ndef flags():\n    torch.backends.cudnn.deterministic = True\n"
    ev = make_evidence({"seeding.py": helper})
    assert ev.py.assigns("torch.backends.cudnn.deterministic", True)
    assert not ev.py.assigns_referenced("torch.backends.cudnn.deterministic", True)
    ev = make_evidence({"seeding.py": helper + "\nflags()\n"})
    assert ev.py.assigns_referenced("torch.backends.cudnn.deterministic", True)


# -- classes ---------------------------------------------------------------
#
# A class named anywhere outside its own body may be instantiated, subclassed
# or registered, so every method in it counts.

_SEEDING_CLASS = (
    "import numpy as np\n\n"
    "class Experiment:\n"
    "    def __init__(self, seed):\n"
    "        np.random.seed(seed)\n"
)


@pytest.mark.parametrize(
    "use",
    [
        pytest.param("print(Experiment(0))\n", id="module-scope-in-the-defining-file"),
        pytest.param("class Child(Experiment):\n    pass\n", id="base-class"),
        pytest.param("REGISTRY = {'exp': Experiment}\n", id="registry"),
    ],
)
def test_a_method_of_a_named_class_counts(make_evidence, use):
    ev = make_evidence({"sweep.py": _SEEDING_CLASS + "\n" + use})
    assert _seeded(ev)


@pytest.mark.parametrize("use", ["Experiment(0)", "sweep.Experiment(0)"], ids=["bare-name", "attribute"])
def test_a_class_naming_itself_is_not_a_reference(make_evidence, use):
    source = _SEEDING_CLASS + (
        "\n    @classmethod\n"
        "    def make(cls):\n"
        f"        return {use}\n"
    )
    ev = make_evidence({"sweep.py": source})
    # The class is the thing to call, so it is what the finding names.
    assert ev.py.unreferenced_function(ev.py.call_sites("numpy.random.seed")[0]) == "Experiment"
    assert not _seeded(ev)


def test_a_method_named_through_an_attribute_counts_without_its_class(make_evidence):
    # Control: green before classes were tracked.
    source = "import numpy as np\n\nclass Seeder:\n    def seed_all(self):\n        np.random.seed(0)\n"
    ev = make_evidence({"seeder.py": source, "sweep.py": "def go(seeder):\n    seeder.seed_all()\n"})
    assert _seeded(ev)


def test_seeding_in_a_class_body_runs_with_the_module(make_evidence):
    # Control: green before classes were tracked.
    ev = make_evidence({"config.py": "import numpy as np\n\nclass Config:\n    rng = np.random.default_rng(0)\n"})
    assert ev.py.calls_referenced("numpy.random.default_rng")


# -- tests -----------------------------------------------------------------


def test_module_scope_seeding_in_a_test_does_not_count(make_evidence):
    ev = make_evidence({"tests/test_x.py": "import numpy as np\nnp.random.seed(0)\n"})
    assert ev.py.unreferenced_function(ev.py.call_sites("numpy.random.seed")[0]) == ""
    assert not _seeded(ev)


def test_a_test_function_named_by_project_code_does_not_count(make_evidence):
    # ``np.random`` names the fixture ``random`` by attribute.
    conftest = "import numpy\n\ndef random():\n    numpy.random.seed(0)\n"
    ev = make_evidence({"tests/conftest.py": conftest, "train.py": "import numpy as np\nnp.random.rand()\n"})
    assert ev.py.unreferenced_function(ev.py.call_sites("numpy.random.seed")[0]) == "random"
    assert not _seeded(ev)


def test_a_test_module_project_code_imports_counts(make_evidence):
    # Control: an import from a non-test module was already credited.
    ev = make_evidence({"tests/helpers.py": _HELPER, "train.py": "from tests.helpers import seed_all\n"})
    assert _seeded(ev)


# -- notebooks -------------------------------------------------------------


def _notebook(*cells: object, **extra: object) -> str:
    return json.dumps({"cells": list(cells), "metadata": {}, "nbformat": 4, **extra})


def _code(source: object) -> dict[str, object]:
    return {"cell_type": "code", "metadata": {}, "outputs": [], "source": source}


@pytest.mark.parametrize(
    "notebook",
    [
        pytest.param(_notebook(_code(["from seeding import seed_all\n", "seed_all(0)\n"])), id="source-list"),
        pytest.param(_notebook(_code("import seeding\n")), id="source-string"),
        pytest.param(_notebook(_code("from pkg.seeding import seed_all\n")), id="package-import"),
        pytest.param(_notebook(_code("HOOK = seed_all\n")), id="bare-name"),
        pytest.param(
            json.dumps({"worksheets": [{"cells": [{"cell_type": "code", "input": "import seeding\n"}]}], "nbformat": 3}),
            id="nbformat-3",
        ),
        pytest.param(
            _notebook(_code("from importlib import import_module\n"), _code("import_module('seeding')\n")),
            id="alias-from-an-earlier-cell",
        ),
        pytest.param(_notebook(_code("seed_all(0) +\n")), id="cell-that-does-not-parse"),
        pytest.param('{"cells": [seed_all', id="notebook-that-does-not-load"),
    ],
)
def test_a_notebook_reference_counts(make_evidence, notebook):
    ev = make_evidence({"pkg/seeding.py": _HELPER, "experiment.ipynb": notebook})
    assert _seeded(ev)


@pytest.mark.parametrize("magic", ["%matplotlib inline", "!pip install numpy"])
def test_a_magic_line_does_not_send_its_cell_to_the_identifier_fallback(make_evidence, magic):
    # Only a parsed cell tells a comment from a use.
    notebook = _notebook(_code(f"{magic}\n# seed_all is called elsewhere\nimport os\n"))
    ev = make_evidence({"pkg/seeding.py": _HELPER, "experiment.ipynb": notebook})
    assert not _seeded(ev)


@pytest.mark.parametrize(
    "path, notebook",
    [
        pytest.param(
            "experiment.ipynb",
            _notebook({"cell_type": "markdown", "metadata": {}, "source": "seed_all(0)"}),
            id="markdown-cell",
        ),
        pytest.param("tests/check.ipynb", _notebook(_code("from seeding import seed_all\n")), id="test-notebook"),
        pytest.param(
            "experiment.ipynb",
            json.dumps(
                {
                    "worksheets": [
                        {
                            "cells": [
                                {"cell_type": "markdown", "source": "Call seed_all first."},
                                {"cell_type": "code", "input": "import os\n"},
                            ]
                        }
                    ],
                    "nbformat": 3,
                }
            ),
            id="nbformat-3-markdown-cell",
        ),
    ],
)
def test_a_notebook_reference_that_does_not_count(make_evidence, path, notebook):
    ev = make_evidence({"seeding.py": _HELPER, path: notebook})
    assert not _seeded(ev)


def test_a_notebook_is_a_referrer_and_not_a_module(make_evidence):
    ev = make_evidence({"experiment.ipynb": _notebook(_code("import numpy as np\nnp.random.seed(0)\n"))})
    assert [m.path for m in ev.py.modules] == []
    assert [r.path for r in ev.py.referrers] == ["experiment.ipynb"]
    # Seeding in the notebook itself is the notebook rules' concern.
    assert not ev.py.call_sites("numpy.random.seed")


# -- packaging entry points ------------------------------------------------
#
# A launcher pip generates imports the module an entry point names, so the
# module counts as imported. ``pkg/commands.py`` is not an entrypoint name.


@pytest.mark.parametrize(
    "path, text",
    [
        pytest.param("pyproject.toml", '[project.scripts]\ntrain = "pkg.commands:main"\n', id="project-scripts"),
        pytest.param("pyproject.toml", '[project.gui-scripts]\ntrain = "pkg.commands:main"\n', id="gui-scripts"),
        pytest.param(
            "pyproject.toml", '[project.entry-points."pkg.plugins"]\ntrain = "pkg.commands:Cli.run"\n', id="entry-points"
        ),
        pytest.param("pyproject.toml", '[tool.poetry.scripts]\ntrain = "pkg.commands:main"\n', id="poetry-scripts"),
        pytest.param(
            "pyproject.toml",
            '[tool.poetry.scripts]\ntrain = { callable = "pkg.commands:main" }\n',
            id="poetry-callable",
        ),
        pytest.param(
            "pyproject.toml",
            '[tool.poetry.scripts]\ntrain = { reference = "pkg.commands:main", type = "console" }\n',
            id="poetry-reference",
        ),
        pytest.param(
            "pyproject.toml", '[tool.poetry.plugins."pkg.plugins"]\ntrain = "pkg.commands:main"\n', id="poetry-plugins"
        ),
        pytest.param(
            "setup.cfg",
            "[metadata]\nname = pkg\n\n[options.entry_points]\nconsole_scripts =\n    train = pkg.commands:main\n",
            id="setup-cfg",
        ),
        pytest.param(
            "setup.cfg",
            "[metadata]\nname = pkg\n\n[options.entry_points]\nconsole_scripts = train = pkg.commands:main\n",
            id="setup-cfg-single-line",
        ),
        pytest.param(
            "setup.py",
            "from setuptools import setup\n"
            "setup(entry_points={'console_scripts': ['train=pkg.commands:main [cuda]']})\n",
            id="setup-py-list",
        ),
        pytest.param(
            "setup.py",
            'from setuptools import setup\nsetup(entry_points="""\n[console_scripts]\ntrain = pkg.commands:main\n""")\n',
            id="setup-py-ini-string",
        ),
    ],
)
def test_an_entry_point_imports_its_module(make_evidence, path, text):
    ev = make_evidence({"pkg/__init__.py": "", "pkg/commands.py": _HELPER, path: text})
    assert _seeded(ev)


def test_an_entry_point_in_a_src_layout_matches_its_package(make_evidence):
    ev = make_evidence(
        {"src/pkg/commands.py": _HELPER, "pyproject.toml": '[project.scripts]\ntrain = "pkg.commands:main"\n'}
    )
    assert _seeded(ev)


def test_an_entry_point_with_padding_inside_its_quotes_counts(make_evidence):
    # Control: green on the first cut. Pins the strip before the target match.
    ev = make_evidence(
        {"pkg/__init__.py": "", "pkg/commands.py": _HELPER, "pyproject.toml": '[project.scripts]\ntrain = " pkg.commands:main "\n'}
    )
    assert _seeded(ev)


def test_an_entry_point_names_its_object(make_evidence):
    # The module ``pkg`` matches no file here, so only the name can give credit.
    ev = make_evidence({"lib_seeding.py": _HELPER, "pyproject.toml": '[project.scripts]\ntrain = "pkg:seed_all"\n'})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "path, text",
    [
        pytest.param("setup.cfg", "[flake8]\nper-file-ignores = pkg.commands:E501\n", id="other-setup-cfg-section"),
        pytest.param("pyproject.toml", '[tool.other]\ntrain = "pkg.commands:main"\n', id="other-pyproject-table"),
        pytest.param("pyproject.toml", '[project.scripts\ntrain = "pkg.commands:main"\n', id="invalid-toml"),
    ],
)
def test_text_that_declares_no_entry_point_gives_no_credit(make_evidence, path, text):
    ev = make_evidence({"pkg/__init__.py": "", "pkg/commands.py": _HELPER, path: text})
    assert not _seeded(ev)


# -- entrypoint files ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "main.py",
        "train.py",
        "run.py",
        "cli.py",
        "app.py",
        "experiment.py",
        "evaluate.py",
        "eval.py",
        "a/b/train.py",
    ],
)
def test_every_function_in_an_entrypoint_file_counts(make_evidence, path):
    ev = make_evidence({path: _HELPER})
    assert path in ev.env.entrypoint_files
    assert _seeded(ev)


def test_an_entrypoint_name_too_deep_to_be_an_entrypoint_does_not_count(make_evidence):
    ev = make_evidence({"a/b/c/train.py": _HELPER})
    assert ev.env.entrypoint_files == []
    assert not _seeded(ev)


# -- decorators ------------------------------------------------------------
#
# A decorator receives the function or class it decorates, and can register
# it for a dispatcher that calls it by a name the source never spells out.


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import click\nimport numpy as np\n\n@click.group()\ndef cli():\n    pass\n\n"
            "@cli.command()\ndef fit():\n    np.random.seed(0)\n\ncli()\n",
            id="click-command",
        ),
        pytest.param(
            "import typer\nimport numpy as np\n\napp = typer.Typer()\n\n"
            "@app.command()\ndef fit():\n    np.random.seed(0)\n\napp()\n",
            id="typer-command",
        ),
        pytest.param(
            "import numpy as np\nfrom registry import register\n\n"
            "@register('cifar')\ndef run_cifar():\n    np.random.seed(0)\n",
            id="registered-function",
        ),
        pytest.param(
            "import numpy as np\nfrom registry import register\n\n"
            "@register('cifar')\nclass Cifar:\n    def setup(self):\n        np.random.seed(0)\n",
            id="registered-class",
        ),
    ],
)
def test_a_decorated_definition_counts(make_evidence, source):
    ev = make_evidence({"scripts/lab.py": source})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import random\nimport numpy as np\nfrom ._registry import register_model\n\n"
            "@register_model\ndef resnet_seeded():\n    random.seed(0)\n    np.random.seed(0)\n",
            id="bare-registered-function",
        ),
        pytest.param(
            "import numpy as np\nfrom registry import register\n\n"
            "@register\nclass Cifar:\n    def setup(self):\n        np.random.seed(0)\n",
            id="bare-registered-class",
        ),
    ],
)
def test_a_bare_decorator_counts(make_evidence, source):
    # A decorator written without a call (timm's ``@register_model``) receives
    # the definition just as a call-form one does.
    ev = make_evidence({"models/zoo.py": source})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import click\nimport numpy as np\n\n@click.command()\ndef fit():\n    pass\n\n"
            "def helper():\n    np.random.seed(0)\n",
            id="undecorated-neighbour",
        ),
        pytest.param(
            "import numpy as np\nfrom registry import register\n\n"
            "def outer():\n    @register\n    def inner():\n        np.random.seed(0)\n",
            id="inside-an-unnamed-function",
        ),
    ],
)
def test_a_decorator_counts_only_for_what_it_decorates(make_evidence, source):
    # Control: green before decorators counted. Pins that a decorator credits
    # its own definition, and only when the code around it runs.
    ev = make_evidence({"scripts/lab.py": source})
    assert not _seeded(ev)


# -- scripts without a .py suffix ------------------------------------------

_SCRIPT = "from pkg.seeding import seed_all\nseed_all(0)\n"
_LISTED = "from setuptools import setup\nsetup(scripts=['bin/lab-train'])\n"


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"bin/lab-train": "#!/usr/bin/env python\n" + _SCRIPT}, id="env-shebang"),
        pytest.param({"scripts/lab-train": "#!/usr/bin/python3\n" + _SCRIPT}, id="path-shebang"),
        pytest.param({"lab-train": "#!python\n" + _SCRIPT}, id="top-level"),
        pytest.param(
            {"bin/lab-train": "#!/usr/bin/env python\nprint 'py2'\nseed_all(0)\n"}, id="shebang-script-that-does-not-parse"
        ),
        # Inventory order puts bin/ before setup.py and tools/ after it.
        pytest.param({"bin/lab-train": _SCRIPT, "setup.py": _LISTED}, id="listed-and-read-first"),
        pytest.param(
            {"tools/lab-train": _SCRIPT, "setup.py": _LISTED.replace("['bin/lab-train']", "('./tools/lab-train',)")},
            id="listed-and-read-last",
        ),
        pytest.param({"python/bin/lab-train": _SCRIPT, "python/setup.py": _LISTED}, id="listed-by-a-nested-setup-py"),
    ],
)
def test_a_python_script_without_a_suffix_counts(make_evidence, files):
    ev = make_evidence({"pkg/seeding.py": _HELPER, **files})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"bin/lab-train": _SCRIPT}, id="unlisted-without-a-shebang"),
        pytest.param({"bin/lab-train": _SCRIPT, "setup.py": _LISTED.replace("bin/", "tools/")}, id="another-file-listed"),
        pytest.param(
            {"bin/lab-train": "#!/bin/sh\npython -c 'from pkg.seeding import seed_all; seed_all(0)'\n"}, id="shell-script"
        ),
        pytest.param(
            {"bin/lab-train": "python -c 'from pkg.seeding import seed_all; seed_all(0)'\n", "setup.py": _LISTED},
            id="listed-shell-script",
        ),
        pytest.param({"tests/bin/check": "#!/usr/bin/env python\n" + _SCRIPT}, id="test-script"),
        pytest.param({"bin/lab-train": "#!/usr/bin/env python\n" + _SCRIPT + "#" * 2_000_000}, id="too-large"),
    ],
)
def test_an_extensionless_file_that_is_not_a_project_script_gives_no_credit(make_evidence, files):
    # Control: green before scripts were read. Pins the shebang, the setup.py
    # listing, the test filter and the size cap.
    ev = make_evidence({"pkg/seeding.py": _HELPER, **files})
    assert not _seeded(ev)


# -- config files that name project code -----------------------------------


@pytest.mark.parametrize(
    "path, text",
    [
        pytest.param("configs/seed.yaml", "_target_: pkg.seeding.seed_all\nseed: 0\n", id="hydra-yaml"),
        pytest.param("configs/seed.yml", "seeder:\n  _target_: 'pkg.seeding.seed_all'\n", id="hydra-yml"),
        pytest.param("configs/seed.json", '{"seeder": {"_target_": "pkg.seeding.seed_all"}}\n', id="hydra-json"),
        pytest.param("configs/seed.toml", '[seeder]\n_target_ = "pkg.seeding.seed_all"\n', id="hydra-toml"),
        pytest.param("configs/fit.yaml", "callbacks:\n  - class_path: pkg.seeding.seed_all\n", id="class-path"),
        pytest.param("configs/train.gin", "seeding.seed_all.seed = 0\n", id="gin-binding"),
        pytest.param("configs/train.gin", "eval/seed_all.seed = 1\n", id="gin-scoped-binding"),
        pytest.param("configs/train.gin", "train.callback = @pkg.seeding.seed_all\n", id="gin-reference"),
        pytest.param("configs/train.gin", "train.callbacks = [@eval/seed_all()]\n", id="gin-scoped-reference"),
    ],
)
def test_a_config_file_naming_the_helper_counts(make_evidence, path, text):
    ev = make_evidence({"pkg/seeding.py": _HELPER, path: text})
    assert _seeded(ev)


def test_a_class_a_config_file_names_counts_for_its_methods(make_evidence):
    model = "import numpy as np\n\nclass Net:\n    def configure_model(self):\n        np.random.seed(0)\n"
    ev = make_evidence({"pkg/model.py": model, "configs/fit.yaml": "model:\n  class_path: pkg.model.Net\n"})
    assert _seeded(ev)


@pytest.mark.parametrize(
    "path, text",
    [
        pytest.param("configs/seed.yaml", "seed_all: true\n", id="a-key-of-the-same-name"),
        pytest.param("configs/seed.txt", "_target_: pkg.seeding.seed_all\n", id="other-suffix"),
        pytest.param("configs/seed.toml", "seed_all.enabled = true\n", id="toml-dotted-key"),
        pytest.param("tests/configs/seed.yaml", "_target_: pkg.seeding.seed_all\n", id="test-config"),
        pytest.param(".adduce/cache/remote.json", '{"_target_": "pkg.seeding.seed_all"}\n', id="adduce-cache"),
        pytest.param("configs/seed.yaml", "_target_: pkg.seeding.seed_all\n" + "#" * 2_000_000, id="too-large"),
    ],
)
def test_a_config_file_that_names_no_target_gives_no_credit(make_evidence, path, text):
    # Control: green before config files were read. Pins the keys, the
    # suffixes, the test filter, the cache exclusion and the size cap.
    ev = make_evidence({"pkg/seeding.py": _HELPER, path: text})
    assert not _seeded(ev)


# -- a notebook that does not load -----------------------------------------
#
# A merge conflict breaks the JSON. Its code cells still give credit, and its
# outputs, which hold the large base64 payloads, add nothing.


def _conflicted(notebook: str) -> str:
    return "<<<<<<< HEAD\n" + notebook + "\n=======\n" + notebook + "\n>>>>>>> theirs\n"


def _with_output(source: str, output: dict[str, object]) -> str:
    cell = {**_code(source), "outputs": [output]}
    return json.dumps({"cells": [cell], "metadata": {}, "nbformat": 4}, indent=1)


@pytest.mark.parametrize(
    "notebook",
    [
        pytest.param(_conflicted(_notebook(_code(["from pkg.seeding import seed_all\n", "seed_all(0)\n"]))), id="source"),
        # nbformat 3 keeps code in ``input`` and markdown in ``source``.
        pytest.param(
            _conflicted(
                json.dumps(
                    {
                        "worksheets": [
                            {
                                "cells": [
                                    {"cell_type": "markdown", "source": "Notes"},
                                    {"cell_type": "code", "input": "seed_all(0)\n"},
                                ]
                            }
                        ]
                    }
                )
            ),
            id="nbformat-3-input",
        ),
        # Without a separator the two cells would read as ``osseed_all``.
        pytest.param(_conflicted(_notebook(_code("import os"), _code("seed_all(0)"))), id="cells-without-newlines"),
        pytest.param(_conflicted('{"cells": [{"source": "seed_all(0) \\q"}]}'), id="source-with-a-bad-escape"),
    ],
)
def test_the_code_cells_of_a_notebook_that_does_not_load_count(make_evidence, notebook):
    # Control: green when every identifier in the text counted. Pins that
    # skipping outputs keeps the cell sources.
    ev = make_evidence({"pkg/seeding.py": _HELPER, "experiment.ipynb": notebook})
    assert ev.py.referrers[0].parse_error
    assert _seeded(ev)


def test_the_outputs_of_a_notebook_that_does_not_load_give_no_credit(make_evidence):
    output = {"output_type": "stream", "name": "stdout", "text": ["seed_all(0) done\n"]}
    ev = make_evidence({"pkg/seeding.py": _HELPER, "experiment.ipynb": _conflicted(_with_output("import os\n", output))})
    assert not _seeded(ev)


def test_an_image_in_a_notebook_that_does_not_load_adds_no_names(make_evidence):
    import base64
    import random

    payload = base64.b64encode(random.Random(0).randbytes(300_000)).decode()
    output = {"output_type": "display_data", "data": {"image/png": payload}, "metadata": {}}
    ev = make_evidence({"experiment.ipynb": _conflicted(_with_output("import os\n", output))})
    (notebook,) = ev.py.referrers
    assert notebook.parse_error
    assert notebook.references <= {"import", "os"}


@pytest.mark.parametrize("length, counts", [(64, True), (65, False)])
def test_a_module_that_does_not_parse_names_only_identifiers_short_enough_to_be_names(make_evidence, length, counts):
    name = "seed_" + "x" * (length - 5)
    helper = f"import numpy as np\n\ndef {name}():\n    np.random.seed(0)\n"
    ev = make_evidence({"seeding.py": helper, "tools/go.py": f"print 'py2'\n{name}()\n"})
    assert _seeded(ev) is counts


# -- imports ---------------------------------------------------------------


@pytest.mark.parametrize("train", ["import pkg.models\n", "from pkg.models import Net\n"], ids=["import", "from-import"])
def test_importing_a_submodule_imports_its_package(make_evidence, train):
    # Control: green on the first cut. Python runs pkg/__init__.py before
    # pkg/models.py, so each prefix of an import counts.
    ev = make_evidence({"pkg/__init__.py": _HELPER, "pkg/models.py": "Net = 1\n", "tools/go.py": train})
    assert _seeded(ev)


def test_a_relative_name_given_to_import_module_imports_the_module(make_evidence):
    # Control: green on the first cut. Pins the leading dots being dropped.
    go = "import importlib\nimportlib.import_module('.seeding', 'pkg')\n"
    ev = make_evidence({"pkg/__init__.py": "", "pkg/seeding.py": _HELPER, "pkg/go.py": go})
    assert _seeded(ev)


def test_the_standalone_collector_reads_every_kind_of_referrer(make_repo):
    from adduce.evidence import collect
    from adduce.evidence.python_ast import collect_python

    repo = make_repo(
        {
            "pkg/seeding.py": _HELPER,
            "experiment.ipynb": _notebook(_code("from pkg.seeding import seed_all\n")),
            "configs/seed.yaml": "_target_: pkg.seeding.seed_all\n",
            "bin/lab-train": "#!/usr/bin/env python\n" + _SCRIPT,
            "pyproject.toml": '[project.scripts]\ntrain = "pkg.seeding:seed_all"\n',
        }
    )
    standalone = collect_python(repo)
    expected = ["bin/lab-train", "configs/seed.yaml", "experiment.ipynb", "pyproject.toml"]
    assert sorted(r.path for r in standalone.referrers) == expected
    assert sorted(r.path for r in collect(repo).py.referrers) == expected
    assert standalone.calls_referenced("numpy.random.seed")
