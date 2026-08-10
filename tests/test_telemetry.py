"""Stage timing and work counters, and the byte-stability they must not cost."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from adduce.engine import run_check
from adduce.report.json_report import render as render_json
from adduce.rules import discover_rules
from adduce.telemetry import Telemetry

_REPO = {
    "README.md": "# Demo\n\n## Reproducing results\n\n```bash\npython train.py\n```\n",
    "requirements.txt": "torch==2.1.0\n",
    "train.py": "import torch\n\ntorch.manual_seed(0)\n",
    "broken.py": "def oops(:\n",
}


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    for relative, content in _REPO.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


# -- primitives -------------------------------------------------------------


def test_stage_accumulates_across_repeat_entries() -> None:
    telemetry = Telemetry()
    for _ in range(3):
        with telemetry.stage("parse"):
            pass
    once = Telemetry()
    with once.stage("parse"):
        pass
    assert telemetry.milliseconds("parse") is not None
    assert once.milliseconds("parse") is not None
    assert telemetry.milliseconds("parse") >= once.milliseconds("parse")
    assert list(telemetry.snapshot()["stage_milliseconds"]) == ["parse"]


def test_stage_records_the_duration_even_when_the_body_raises() -> None:
    telemetry = Telemetry()
    with pytest.raises(ValueError, match="boom"), telemetry.stage("scan"):
        raise ValueError("boom")
    assert telemetry.milliseconds("scan") is not None


def test_counters_accumulate_and_default_to_zero() -> None:
    telemetry = Telemetry()
    telemetry.count("files")
    telemetry.count("files", 4)
    assert telemetry.counter("files") == 5
    assert telemetry.counter("never-set") == 0


def test_snapshot_is_sorted_and_json_serialisable() -> None:
    telemetry = Telemetry()
    for name in ("zeta", "alpha", "mu"):
        with telemetry.stage(name):
            pass
        telemetry.count(name)
    snapshot = telemetry.snapshot()
    assert list(snapshot["stage_milliseconds"]) == ["alpha", "mu", "zeta"]
    assert list(snapshot["counters"]) == ["alpha", "mu", "zeta"]
    json.dumps(snapshot, allow_nan=False)


def test_telemetry_module_imports_nothing_that_leaves_the_process() -> None:
    """A timer must never grow a writer or a socket."""
    source = Path(__file__).resolve().parents[1] / "src" / "adduce" / "telemetry.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"__future__", "time", "collections", "contextlib", "dataclasses", "typing"}


# -- pipeline integration ---------------------------------------------------


def test_run_check_times_every_stage_and_counts_the_work(repo_root: Path) -> None:
    result = run_check(repo_root)
    stages = result.telemetry.snapshot()["stage_milliseconds"]
    counters = result.telemetry.snapshot()["counters"]

    for expected in ("total", "scan", "collect.python", "rules.evaluate", "score", "graph"):
        assert expected in stages, expected
    assert stages["total"] >= stages["scan"]

    assert counters["files.inventoried"] == len(_REPO)
    assert counters["files.python"] == 2
    assert counters["parse.python.modules"] == 2
    assert counters["parse.python.failed"] == 1
    assert counters["files.read_from_disk"] > 0


def test_rule_counters_account_for_every_discovered_rule(repo_root: Path) -> None:
    rules = discover_rules(include_plugins=False)
    result = run_check(repo_root, rules=rules)
    counters = result.telemetry.snapshot()["counters"]
    accounted = (
        counters.get("rules.evaluated", 0)
        + counters.get("rules.skipped_inapplicable", 0)
        + counters.get("rules.skipped_disabled", 0)
    )
    assert accounted == len(rules)
    assert counters["rules.evaluated"] == len(result.card.findings)


# -- the reason reporting is opt-in ----------------------------------------


def test_json_report_omits_telemetry_by_default(repo_root: Path) -> None:
    """The validation harness compares this document byte for byte.

    Durations differ between identical runs, so an unconditional timing block
    would make every determinism comparison fail.
    """
    result = run_check(repo_root)
    assert result.telemetry.report is False
    payload = json.loads(render_json(result))
    assert "telemetry" not in payload


def test_json_report_includes_telemetry_when_asked(repo_root: Path) -> None:
    result = run_check(repo_root)
    result.telemetry.report = True
    payload = json.loads(render_json(result))
    assert set(payload["telemetry"]) == {"stage_milliseconds", "counters"}
    assert payload["telemetry"]["counters"]["files.inventoried"] == len(_REPO)


def test_default_report_is_byte_identical_with_and_without_collection(repo_root: Path) -> None:
    """Collection is always on; only reporting is conditional."""
    first = render_json(run_check(repo_root))
    second = render_json(run_check(repo_root))
    assert first == second
    assert "stage_milliseconds" not in first
