"""Two boundaries: what a rule pack may call itself, and how deep a parse may go.

The identity half installs real ``Rule`` subclasses on the ``adduce.rules``
entry point, because the read order matters: the registry reads ``id`` once to
admit a rule, so the engine's read is the second one, and a rule whose answer
changes between them is admitted under one id and evaluated under another.
Handing the same class to ``run_check(rules=...)`` consumes no read and lands
in a different window, which ``test_engine`` already covers.

The parser half is about ``RecursionError``. It is a ``RuntimeError``, so the
narrow ``except`` clauses around each parse never caught it, and a deeply
nested but perfectly well-formed document made ``adduce check`` print a
traceback and exit 1 -- the code that means the quality gate failed.
"""

from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass
from functools import cache
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from adduce.cli import app
from adduce.engine import run_check
from adduce.evidence.config import collect_config, flatten
from adduce.evidence.notebook import collect_notebooks
from adduce.evidence.results import ResultFile, _collect_bounded, _collect_scalars, collect_results
from adduce.ledger import Ledger, load_ledger, write_ledger
from adduce.manifest import load_manifest
from adduce.model import scan_repository
from adduce.report.json_report import render as render_json
from adduce.rules import BUILTIN_RULES, registry
from adduce.rules.base import Category, Finding, Rule, Status
from adduce.rules.registry import RulePluginWarning
from adduce.safe_write import SafeWriteError
from tests.conftest import plain
from tests.test_engine import WELL_FORMED, _write

runner = CliRunner()


# -- the plugin identity boundary -------------------------------------------

#: Text only the rule pack writes. Finding it anywhere in a rendered report
#: means a refused rule still spoke.
PACK_MESSAGE = "forged: this line was written by the rule pack"


def _builtin(rule_id: str) -> type[Rule]:
    return next(rule_class for rule_class in BUILTIN_RULES if rule_class.id == rule_id)


@dataclass
class FakeEntryPoint:
    name: str
    module: str
    value: str
    target: object | None = None

    @property
    def dist(self) -> SimpleNamespace:
        return SimpleNamespace(name=f"distribution-{self.name}")

    def load(self) -> object:
        return self.target


def _install_pack(monkeypatch, *rule_classes: type[Rule]) -> None:
    """Register ``rule_classes`` as one installed pack on the entry point."""
    entry = FakeEntryPoint(
        "identity-pack",
        "example.pack",
        "example.pack:plugin",
        SimpleNamespace(RULES=rule_classes),
    )
    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [entry])


def _plugin_warnings(caught) -> list[str]:
    return [str(w.message) for w in caught if w.category is RulePluginWarning]


class LyingId(str):
    """A ``str`` that answers every comparison the way its owner wants.

    ``__hash__`` is constant and deliberately not the real string's hash, so a
    set membership test handed this object misses the bucket its characters
    belong in.
    """

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


class ShiftingIdRule(Rule):
    """Its own id on the first read, a built-in's on every read after."""

    category = Category.DOCUMENTATION
    title = "Changes its mind about which rule it is"
    weight = 1

    def __init__(self) -> None:
        self._reads = 0

    @property
    def id(self) -> str:
        self._reads += 1
        return "X-SHIFT-001" if self._reads == 1 else "R-DET-001"

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id="R-DET-001",
            category=Category.DETERMINISM,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class LyingAdmissionRule(Rule):
    """Files under a built-in's id through an object that lies when compared."""

    id = LyingId("R-EXEC-001")
    category = Category.CODE_EXECUTION
    title = "Claims to be whichever rule it is asked about"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id="R-EXEC-001",
            category=Category.CODE_EXECUTION,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class LyingComparisonRule(Rule):
    """A clean id of its own, then a finding filed under a built-in's.

    The lie is in the comparison rather than in the id: Python offers a ``str``
    subclass's reflected ``__eq__`` first, so an engine that compares the
    reported id against this object gets the answer this object chose.
    """

    id = LyingId("X-LIE-002")
    category = Category.DOCUMENTATION
    title = "Reports under another rule's id"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id="R-DET-001",
            category=Category.DETERMINISM,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class LyingFindingRule(Rule):
    """A clean id, and a finding whose own id is the object that lies.

    Nothing pins the id a finding reports, so this is the lie the engine has to
    survive after the rule has already been identified honestly.
    """

    id = "X-LIE-005"
    category = Category.DOCUMENTATION
    title = "Returns a finding that claims to be any rule"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id=LyingId("R-DET-001"),
            category=Category.DETERMINISM,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class StringCategoryRule(Rule):
    """Declares a category that is a plain string, not a :class:`Category`."""

    id = "X-CAT-003"
    category = "Documentation"  # type: ignore[assignment]
    title = "Declares a category no reporter can read"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id="X-CAT-003",
            category=self.category,
            title=self.title,
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class PlainCollidingRule(Rule):
    """An ordinary ``str`` id equal to a built-in's."""

    id = "R-LIC-001"
    category = Category.ACCESS_LEGAL
    title = "Takes a built-in's place with no trickery at all"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return Finding(
            rule_id="R-LIC-001",
            category=Category.ACCESS_LEGAL,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message=PACK_MESSAGE,
            remediation="",
            weight=1,
        )


class WellBehavedRule(Rule):
    """A third-party rule with an id of its own and a real category."""

    id = "X-PACK-004"
    category = Category.DOCUMENTATION
    title = "A pack rule that plays by the rules"
    weight = 1

    def evaluate(self, ev) -> Finding:
        return self.finding(Status.PASS, 1.0, "detected the pack's own signal")


def test_an_id_that_shifts_after_admission_cannot_take_a_builtins_place(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, ShiftingIdRule)

    with pytest.warns(RulePluginWarning):
        result = run_check(tmp_path)

    seeded = [f for f in result.card.findings if f.rule_id == "R-DET-001"]
    assert len(seeded) == 1
    assert seeded[0].title == _builtin("R-DET-001").title
    assert seeded[0].message != PACK_MESSAGE
    assert PACK_MESSAGE not in render_json(result)
    assert not any(f.rule_id == "X-SHIFT-001" for f in result.card.findings)
    counters = result.telemetry.snapshot()["counters"]
    assert counters["rules.skipped_unidentifiable"] == 1


def test_an_id_that_lies_when_compared_is_refused_at_admission(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, LyingAdmissionRule)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path)

    (warning,) = _plugin_warnings(caught)
    assert "R-EXEC-001" in warning
    assert "conflicts with an existing rule" in warning

    entrypoints = [f for f in result.card.findings if f.rule_id == "R-EXEC-001"]
    assert len(entrypoints) == 1
    assert entrypoints[0].title == _builtin("R-EXEC-001").title
    assert PACK_MESSAGE not in render_json(result)


def test_an_id_that_lies_when_compared_cannot_pass_off_a_forged_finding(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, LyingComparisonRule)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path)

    assert any("reported under another rule's id" in text for text in _plugin_warnings(caught))

    seeded = [f for f in result.card.findings if f.rule_id == "R-DET-001"]
    assert len(seeded) == 1
    assert seeded[0].title == _builtin("R-DET-001").title
    (degraded,) = [f for f in result.card.findings if f.rule_id == "X-LIE-002"]
    assert degraded.status is Status.UNKNOWN
    assert PACK_MESSAGE not in render_json(result)


def test_a_finding_whose_own_id_lies_is_not_taken_at_its_word(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, LyingFindingRule)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path)

    assert any("reported under another rule's id" in text for text in _plugin_warnings(caught))

    seeded = [f for f in result.card.findings if f.rule_id == "R-DET-001"]
    assert len(seeded) == 1
    assert seeded[0].title == _builtin("R-DET-001").title
    (degraded,) = [f for f in result.card.findings if f.rule_id == "X-LIE-005"]
    assert degraded.status is Status.UNKNOWN
    assert PACK_MESSAGE not in render_json(result)


def test_a_category_that_is_not_a_category_leaves_the_report_renderable(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, StringCategoryRule)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path)

    assert any("could not supply the identity" in text for text in _plugin_warnings(caught))
    assert not any(f.rule_id == "X-CAT-003" for f in result.card.findings)
    assert result.telemetry.snapshot()["counters"]["rules.skipped_unidentifiable"] == 1

    payload = json.loads(render_json(result))
    assert payload["findings"]
    assert PACK_MESSAGE not in json.dumps(payload)


def test_a_plain_colliding_id_is_still_refused(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, PlainCollidingRule)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path)

    (warning,) = _plugin_warnings(caught)
    assert "R-LIC-001" in warning
    assert "conflicts with an existing rule" in warning

    licensed = [f for f in result.card.findings if f.rule_id == "R-LIC-001"]
    assert len(licensed) == 1
    assert licensed[0].title == _builtin("R-LIC-001").title
    assert PACK_MESSAGE not in render_json(result)


def test_a_well_behaved_pack_rule_still_runs_and_still_appears(tmp_path, monkeypatch):
    """A guard that contains everything is not a guard."""
    _write(tmp_path, WELL_FORMED)
    _install_pack(monkeypatch, WellBehavedRule)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run_check(tmp_path)

    assert _plugin_warnings(caught) == []
    (finding,) = [f for f in result.card.findings if f.rule_id == "X-PACK-004"]
    assert finding.status is Status.PASS
    assert finding.category is Category.DOCUMENTATION
    assert "X-PACK-004" in render_json(result)


# -- parser limits ----------------------------------------------------------


def _deep_json_object(depth: int) -> str:
    return '{"a": ' * depth + "1" + "}" * depth


@cache
def _depth_the_json_parser_refuses() -> int:
    """The shallowest probed depth at which ``json.loads`` gives up.

    Measured rather than assumed: the C scanner's ceiling follows the
    interpreter's stack, not ``sys.setrecursionlimit``, and moves with the
    version and the platform. A hardcoded depth would quietly stop reaching the
    guard on an interpreter that parses deeper than it.
    """
    depth = 1_000
    while depth <= 262_144:
        try:
            json.loads(_deep_json_object(depth))
        except RecursionError:
            return depth
        depth *= 4
    return 0


@cache
def _depth_python_cannot_walk_but_json_parses() -> int:
    """A depth in the gap between the two ceilings.

    A pure-Python walk is bounded by ``sys.getrecursionlimit()``; the JSON C
    scanner is bounded by the stack. In the gap a document parses completely
    and then cannot be walked, which is where a partial read that looks
    complete would come from.
    """
    depth = sys.getrecursionlimit() * 4
    while depth > sys.getrecursionlimit():
        try:
            json.loads(_deep_json_object(depth))
        except RecursionError:
            depth //= 2
        else:
            return depth
    return 0


def _beyond_the_json_parser() -> str:
    depth = _depth_the_json_parser_refuses()
    if not depth:
        pytest.skip("this interpreter parses JSON nested deeper than this test builds")
    return _deep_json_object(depth)


def _beyond_a_python_walk() -> tuple[str, dict[str, Any]]:
    """A document that parses whole and a mapping no Python walk can finish."""
    depth = _depth_python_cannot_walk_but_json_parses()
    if not depth:
        pytest.skip("this interpreter's JSON ceiling is below its Python recursion limit")
    text = '{"accuracy": 0.4242, "nested": ' + _deep_json_object(depth) + "}"
    return text, json.loads(text)


#: Deep enough that the pure-Python YAML parser cannot reach the bottom under
#: any plausible recursion limit: each flow level costs several frames, so this
#: is thousands of frames past the 1000-frame default.
_DEEP_YAML = "learning_rate: 0.4242\nnested: " + "[" * 4_000 + "]" * 4_000


def _check_json(tmp_path) -> dict:
    """Run ``adduce check`` and return the report, asserting the contract.

    Exit 0, no traceback, and the rest of the repository still assessed: a file
    the parser gave up on is one file yielding no evidence, not a failed run.
    """
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, plain(result.output)
    assert not isinstance(result.exception, RecursionError)
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert {"R-DET-001", "R-LIC-001"} <= {f["rule_id"] for f in payload["findings"]}
    return payload


def _config_values(root) -> tuple[set[str], set[Any]]:
    config = collect_config(scan_repository(root))
    paths = {f.path for f in config.files}
    values = {v for f in config.files for v in f.values.values() if isinstance(v, (int, float, str))}
    return paths, values


def test_a_config_too_deep_to_parse_yields_no_values_and_no_failure(tmp_path):
    files = dict(WELL_FORMED)
    files["configs/deep.yaml"] = _DEEP_YAML
    _write(tmp_path, files)
    with pytest.raises(RecursionError):
        yaml.safe_load(_DEEP_YAML)

    paths, values = _config_values(tmp_path)

    assert "configs/main.yaml" in paths
    assert "configs/deep.yaml" not in paths
    assert 0.4242 not in values
    _check_json(tmp_path)


def test_a_config_too_deep_to_flatten_contributes_no_values(tmp_path):
    text, data = _beyond_a_python_walk()
    files = dict(WELL_FORMED)
    files["configs/deep.json"] = text
    _write(tmp_path, files)
    with pytest.raises(RecursionError):
        flatten(data)

    paths, values = _config_values(tmp_path)

    assert "configs/main.yaml" in paths
    assert "configs/deep.json" not in paths
    assert 0.4242 not in values
    _check_json(tmp_path)


def _result_metrics(root) -> tuple[set[str], set[float]]:
    results = collect_results(scan_repository(root))
    paths = {f.path for f in results.files}
    values = {v for f in results.files for series in f.metrics.values() for v in series}
    return paths, values


def test_a_result_file_too_deep_to_parse_contributes_no_metrics(tmp_path):
    files = dict(WELL_FORMED)
    files["results/summary.json"] = json.dumps({"accuracy": 0.91})
    files["results/deep.json"] = '{"accuracy": 0.4242, "nested": ' + _beyond_the_json_parser() + "}"
    _write(tmp_path, files)

    paths, values = _result_metrics(tmp_path)

    assert "results/summary.json" in paths
    assert "results/deep.json" not in paths
    assert 0.91 in values
    assert 0.4242 not in values
    _check_json(tmp_path)


def test_one_unparseable_jsonl_row_does_not_cost_the_rows_around_it(tmp_path):
    files = dict(WELL_FORMED)
    files["results/metrics.jsonl"] = "\n".join(
        [
            '{"accuracy": 0.4242, "nested": ' + _beyond_the_json_parser() + "}",
            json.dumps({"f1": 0.88}),
        ]
    )
    _write(tmp_path, files)

    results = collect_results(scan_repository(tmp_path))
    (metrics,) = [f.metrics for f in results.files if f.path == "results/metrics.jsonl"]

    assert metrics == {"f1": [0.88]}
    _check_json(tmp_path)


def test_a_result_file_too_deep_to_walk_contributes_no_partial_metrics(tmp_path):
    text, data = _beyond_a_python_walk()
    files = dict(WELL_FORMED)
    files["results/summary.json"] = json.dumps({"accuracy": 0.91})
    files["results/walk.json"] = text
    _write(tmp_path, files)

    # The unbounded walk records the shallow metric and then gives up, which is
    # the partial read that would pass for a complete set of logged numbers.
    partial = ResultFile(path="results/walk.json")
    with pytest.raises(RecursionError):
        _collect_scalars(data, partial)
    assert "accuracy" in partial.metrics

    bounded = ResultFile(path="results/walk.json")
    _collect_bounded(data, bounded)
    assert bounded.metrics == {}

    paths, values = _result_metrics(tmp_path)
    assert "results/summary.json" in paths
    assert "results/walk.json" not in paths
    assert 0.4242 not in values
    _check_json(tmp_path)


_GOOD_NOTEBOOK = json.dumps(
    {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "source": "import torch\n",
                "outputs": [],
                "metadata": {},
            }
        ],
        "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
)


def test_a_notebook_too_deep_to_parse_is_recorded_as_unreadable(tmp_path):
    files = dict(WELL_FORMED)
    files["good.ipynb"] = _GOOD_NOTEBOOK
    files["deep.ipynb"] = _beyond_the_json_parser()
    _write(tmp_path, files)

    notebooks = {n.path: n for n in collect_notebooks(scan_repository(tmp_path)).notebooks}

    assert notebooks["deep.ipynb"].parse_error is True
    assert notebooks["deep.ipynb"].code_cells == 0
    assert notebooks["good.ipynb"].parse_error is False
    assert notebooks["good.ipynb"].code_cells == 1
    _check_json(tmp_path)


def test_a_manifest_too_deep_to_parse_is_a_named_input_failure(tmp_path):
    files = dict(WELL_FORMED)
    files[".adduce/manifest.yaml"] = "schema: adduce/v1\nnested: " + "[" * 4_000 + "]" * 4_000
    _write(tmp_path, files)

    manifest = load_manifest(tmp_path)
    assert manifest.error is not None
    assert "could not parse" in manifest.error

    result = runner.invoke(app, ["check", str(tmp_path)])

    assert result.exit_code == 2, plain(result.output)
    assert not isinstance(result.exception, RecursionError)
    assert "could not parse" in plain(result.output)
    assert "Traceback" not in result.output


def test_a_baseline_too_deep_to_parse_is_reported_as_unusable_not_as_a_regression(tmp_path):
    _write(tmp_path, WELL_FORMED)
    directory = tmp_path / ".adduce"
    directory.mkdir(exist_ok=True)
    (directory / "baseline.json").write_text(_beyond_the_json_parser(), encoding="utf-8")

    result = runner.invoke(app, ["check", str(tmp_path), "--fail-on-regression"])

    assert result.exit_code == 2, plain(result.output)
    assert not isinstance(result.exception, RecursionError)
    assert "invalid baseline" in plain(result.output)
    assert "Traceback" not in result.output


def test_a_ledger_too_deep_to_parse_is_neither_audited_nor_replaced(tmp_path):
    _write(tmp_path, WELL_FORMED)
    artifact = tmp_path / "checklist.md"
    generated = runner.invoke(app, ["checklist", str(tmp_path), "--output", str(artifact)])
    assert generated.exit_code == 0, plain(generated.output)

    ledger_path = tmp_path / ".adduce" / "evidence-ledger.json"
    ledger_path.write_text(_beyond_the_json_parser(), encoding="utf-8")
    recorded = ledger_path.read_bytes()

    with pytest.raises(SafeWriteError, match="refusing to replace an invalid evidence ledger"):
        load_ledger(tmp_path)

    audit = runner.invoke(app, ["audit-generated", str(artifact), str(tmp_path)])
    assert audit.exit_code == 2, plain(audit.output)
    assert not isinstance(audit.exception, RecursionError)
    assert "invalid evidence ledger" in plain(audit.output)
    assert "Traceback" not in audit.output

    rewrite = runner.invoke(app, ["checklist", str(tmp_path)])
    assert rewrite.exit_code == 2, plain(rewrite.output)
    assert "refusing to replace an invalid evidence ledger" in plain(rewrite.output)
    assert ledger_path.read_bytes() == recorded


def test_a_ledger_that_cannot_be_reserialised_is_refused_and_left_alone(tmp_path, monkeypatch):
    """The encoder's own ceiling is out of reach of a test at this cost.

    ``write_ledger`` serialises with ``indent=2``, so the document the encoder
    emits before it gives up grows with the square of the nesting depth: on
    CPython 3.14 the depth where it raises needs about a gigabyte of output
    first, which is not a fixture worth a test suite. The trigger is stubbed;
    the refusal, its message and the untouched file are real.
    """
    _write(tmp_path, WELL_FORMED)
    artifact = tmp_path / "checklist.md"
    generated = runner.invoke(app, ["checklist", str(tmp_path), "--output", str(artifact)])
    assert generated.exit_code == 0, plain(generated.output)

    ledger_path = tmp_path / ".adduce" / "evidence-ledger.json"
    recorded = ledger_path.read_bytes()

    def refuse_to_encode(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(json, "dumps", refuse_to_encode)

    with pytest.raises(SafeWriteError, match="cannot be serialized safely"):
        write_ledger(
            tmp_path,
            Ledger(artifact_path="checklist.md", artifact_sha256="0" * 64, provenance={}),
        )

    monkeypatch.undo()
    assert ledger_path.read_bytes() == recorded
