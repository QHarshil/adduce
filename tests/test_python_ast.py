"""Name resolution, wrapper expansion, and structural extraction from Python sources."""

from __future__ import annotations

import ast
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
    # Deliberate: a primitive appearing anywhere counts, even inside a helper
    # never invoked in the scanned tree (it may be called from a notebook or
    # shell). Erring this way avoids false "you did not seed" findings, at the
    # cost of trusting dead code; full reachability analysis is out of scope.
    assert ev.py.calls("torch.manual_seed")


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


def test_numpy_generator_counts_as_seeding(make_evidence):
    ev = make_evidence({"a.py": "import numpy as np\nrng = np.random.default_rng(42)\n"})
    assert ev.py.uses_numpy_generator
