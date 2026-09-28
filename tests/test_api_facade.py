"""`adduce.api` re-exports exactly the documented plugin surface, and nothing else."""

from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import adduce.api as api

ROOT = Path(__file__).resolve().parents[1]
_TABLE_ROW = re.compile(r"^\| `([A-Za-z_][A-Za-z0-9_]*)` \| `([a-z_.]+)` \|$")


def _documented_surface() -> dict[str, str]:
    text = (ROOT / "docs" / "plugin-api.md").read_text(encoding="utf-8")
    section = text.split("## Public surface", 1)[1].split("\n## ", 1)[0]
    rows = dict(
        match.groups()
        for line in section.splitlines()
        if (match := _TABLE_ROW.match(line.strip()))
    )
    assert rows, "the public surface table was not found"
    return rows


def test_all_matches_the_documented_surface() -> None:
    assert sorted(api.__all__) == sorted(_documented_surface())
    assert len(api.__all__) == len(set(api.__all__))


@pytest.mark.parametrize(("name", "module"), sorted(_documented_surface().items()))
def test_each_name_is_the_object_at_its_documented_path(name: str, module: str) -> None:
    assert getattr(api, name) is getattr(importlib.import_module(module), name)


def test_star_import_binds_exactly_all() -> None:
    namespace: dict[str, object] = {}
    exec("from adduce.api import *", namespace)
    assert sorted(k for k in namespace if k != "__builtins__") == sorted(api.__all__)


def test_the_facade_holds_no_logic() -> None:
    tree = ast.parse((ROOT / "src" / "adduce" / "api.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the docstring
        if isinstance(node, ast.ImportFrom):
            continue
        if isinstance(node, ast.Assign) and [
            target.id for target in node.targets if isinstance(target, ast.Name)
        ] == ["__all__"]:
            continue
        raise AssertionError(f"api.py holds a {type(node).__name__} at line {node.lineno}")


_PROBE = """
import warnings
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    import {first}
import adduce.report
assert "facade-probe" in adduce.report.RENDERERS, sorted(adduce.report.RENDERERS)
failures = [str(w.message) for w in caught if w.category.__name__ == "ReporterPluginWarning"]
assert not failures, failures
print("loaded")
"""


@pytest.mark.parametrize("first", ["adduce.report", "adduce.api"])
def test_a_reporter_plugin_can_import_the_facade_during_discovery(
    tmp_path: Path, first: str
) -> None:
    (tmp_path / "facade_probe_reporter.py").write_text(
        "from adduce.api import Finding, Status  # imported during discovery\n"
        "\n"
        "def render(result):\n"
        "    return 'facade-probe'\n",
        encoding="utf-8",
    )
    dist = tmp_path / "facade_probe_reporter-0.0.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: facade-probe-reporter\nVersion: 0.0.0\n",
        encoding="utf-8",
    )
    (dist / "entry_points.txt").write_text(
        "[adduce.reporters]\nfacade-probe = facade_probe_reporter:render\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), *filter(None, [env.get("PYTHONPATH")])]
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", _PROBE.format(first=first)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("loaded")
