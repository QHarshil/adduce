"""The iterative AST walk: what it skips, and the order it keeps.

The walk no longer descends every node. It skips the subtrees of ``Name``,
``Constant`` and ``alias``, and any node with no fields, because none of them
can enclose an import, a definition, a call or an assignment. That is a claim
about the grammar, and the grammar changes between Python versions — so the
first test re-derives it from parsed source rather than trusting the constant.
It has to run on the oldest supported version to be worth anything, which is
why it reads a corpus that exists in a checkout rather than the pinned clones.

The rest pin the ordering the recursive form used to get from the call stack:
source order, and an enclosing-function attribution that survives nesting.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from adduce.evidence.python_ast import (
    _ELIDED_NODES,
    _module_name_for,
    _ModuleVisitor,
)

#: The node types the visitor extracts. A skipped subtree may contain none.
_EXTRACTED = frozenset(
    {
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Call,
        ast.Assign,
        ast.ClassDef,
    }
)


def _corpus() -> list[tuple[str, ast.Module]]:
    """Diverse real source available wherever the suite runs.

    The analyzer's own tree and the standard library next to it: a few hundred
    modules of ordinary Python, on whatever version is running the test.
    """
    roots = [Path(__file__).resolve().parent.parent / "src", Path(ast.__file__).parent]
    modules: list[tuple[str, ast.Module]] = []
    for root in roots:
        for path in sorted(root.rglob("*.py"))[:400]:
            try:
                modules.append((str(path), ast.parse(path.read_text(encoding="utf-8"))))
            except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
                continue
    return modules


def test_skipped_subtrees_contain_nothing_the_visitor_extracts() -> None:
    """Re-derive the skip set from source on the running Python version.

    If a future grammar gives ``Name`` or ``Constant`` a field that can hold an
    expression, this fails on that version instead of silently dropping
    evidence.
    """
    modules = _corpus()
    assert len(modules) > 100, "corpus too small to be evidence"

    for name, tree in modules:
        for node in ast.walk(tree):
            if node.__class__ not in _ELIDED_NODES:
                continue
            for descendant in ast.walk(node):
                assert descendant.__class__ not in _EXTRACTED, (
                    f"{name}: {node.__class__.__name__} encloses a "
                    f"{descendant.__class__.__name__}, which the walk would skip"
                )


def test_fieldless_nodes_are_never_extracted() -> None:
    """The other half of the skip rule, over every node type in the grammar.

    A node with no fields has no children, so skipping it is only safe while no
    extracted type is fieldless.
    """
    for name in dir(ast):
        node_type = getattr(ast, name)
        if not (isinstance(node_type, type) and issubclass(node_type, ast.AST)):
            continue
        if node_type in _EXTRACTED:
            assert node_type._fields, f"{name} is extracted but has no fields"


def _analyse(source: str) -> _ModuleVisitor:
    visitor = _ModuleVisitor(path="m.py", module_name=_module_name_for(Path("m.py")))
    visitor.visit(ast.parse(source))
    return visitor


def test_calls_are_recorded_in_source_order() -> None:
    """A stack pops last-in first, so children are pushed back to front."""
    visitor = _analyse("import torch\ntorch.a()\ntorch.b()\ntorch.c()\n")
    assert [call.qualname for call in visitor.analysis.calls] == [
        "torch.a",
        "torch.b",
        "torch.c",
    ]


def test_an_alias_only_applies_to_calls_after_it() -> None:
    """Alias resolution depends on the walk reaching imports in source order."""
    visitor = _analyse("th.manual_seed(0)\nimport torch as th\nth.manual_seed(1)\n")
    assert [call.qualname for call in visitor.analysis.calls] == [
        "th.manual_seed",
        "torch.manual_seed",
    ]


def test_nested_functions_unwind_in_the_right_order() -> None:
    """Both enclosing functions get the inner call; neither keeps it after."""
    visitor = _analyse(
        "import torch\n"
        "def outer():\n"
        "    def inner():\n"
        "        torch.manual_seed(0)\n"
        "    torch.cuda.empty_cache()\n"
        "def sibling():\n"
        "    torch.save({}, 'p')\n"
    )
    functions = {name: sorted(calls) for name, calls in visitor.analysis.functions.items()}
    assert functions == {
        "outer": ["torch.cuda.empty_cache", "torch.manual_seed"],
        "inner": ["torch.manual_seed"],
        "sibling": ["torch.save"],
    }
    assert visitor._current_function == []


def test_async_functions_unwind_like_functions() -> None:
    visitor = _analyse("import torch\nasync def load():\n    torch.manual_seed(0)\n")
    assert visitor.analysis.functions == {"load": {"torch.manual_seed"}}
    assert visitor._current_function == []


def test_a_decorator_is_attributed_to_the_function_it_decorates() -> None:
    """``_fields`` puts ``decorator_list`` after ``body``, and the walk keeps it.

    The recursive form read decorators while the function was still on the
    stack. Preserving that is why field order is followed rather than reordered.
    """
    visitor = _analyse("import torch\n@torch.no_grad()\ndef step():\n    pass\n")
    assert visitor.analysis.functions == {"step": {"torch.no_grad"}}


def test_evidence_is_found_below_a_skipped_node_type() -> None:
    """A call inside an f-string sits under nodes the walk still descends."""
    visitor = _analyse("import torch\nx = f'{torch.rand(3)}'\n")
    assert [call.qualname for call in visitor.analysis.calls] == ["torch.rand"]


def test_evidence_is_found_inside_an_annotation() -> None:
    """``arg`` is not skipped, because an annotation can hold a call."""
    visitor = _analyse(
        "import torch\ndef f(x: Annotated[int, torch.zeros(1)]) -> None:\n    pass\n"
    )
    assert [call.qualname for call in visitor.analysis.calls] == ["torch.zeros"]


def test_deep_expression_nesting_does_not_exhaust_the_stack() -> None:
    """The walk is iterative, so depth is bounded by memory, not by frames.

    A left-leaning ``BinOp`` chain nests without brackets, which the tokenizer
    caps. The recursion limit is lowered around the walk only — parsing has its
    own limits and they are not what this pins.
    """
    depth = 500
    source = "import torch\nx = torch.rand(1)" + " + 1" * depth + "\n"
    tree = ast.parse(source)
    visitor = _ModuleVisitor(path="m.py", module_name="m")

    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(100)
    try:
        visitor.visit(tree)
    finally:
        sys.setrecursionlimit(limit)

    assert [call.qualname for call in visitor.analysis.calls] == ["torch.rand"]


@pytest.mark.parametrize(
    "source",
    [
        "",
        "pass\n",
        "x = 1\n",
        "class C:\n    pass\n",
        "def f():\n    pass\n",
    ],
)
def test_trivial_modules_leave_no_residue(source: str) -> None:
    visitor = _analyse(source)
    assert visitor._current_function == []
    assert visitor._stack == []
