"""What an out-of-tree package depends on, checked against a real installation.

`tests/fixtures/external_plugin` builds the distribution `adduce-contract-plugin`:
one rule under the `adduce.rules` group, one reporter plus one deliberate
built-in-name collision under `adduce.reporters`. Nothing here fabricates an
entry point or injects a rule — every assertion runs through
`importlib.metadata` in an interpreter that has the distribution installed
beside adduce:

    pip install tests/fixtures/external_plugin

Absent that installation the tests skip, so the repository suite stays green.
A job that owns this contract must set `ADDUCE_REQUIRE_EXTERNAL_PLUGIN=1`,
which turns the missing distribution into a failure: a skip there would be a
gate that reports green without ever running.

Reporter discovery happens once, when `adduce.report` is first imported, so the
distribution has to be installed before the interpreter starts.
"""

from __future__ import annotations

import json
import os
import re
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from pathlib import Path

import pytest

from adduce.engine import CheckResult, run_check
from adduce.report import RENDERERS, json_report
from adduce.rules import Finding, Status, discover_rules

DISTRIBUTION = "adduce-contract-plugin"
RULE_ID = "X-CONTRACT-PLUGIN-001"
RULE_MODULE = "adduce_contract_plugin.rules"
REPORTER_MODULE = "adduce_contract_plugin.reporter"
FORMAT_NAME = "contract-plugin"
SHADOWED_FORMAT = "json"
REQUIRE_ENV = "ADDUCE_REQUIRE_EXTERNAL_PLUGIN"

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "external_plugin"

_NON_ALPHANUM_RUN = re.compile(r"[-_.]+")


def _canonical(name: object) -> str:
    return _NON_ALPHANUM_RUN.sub("-", str(name)).lower()


def _installed() -> bool:
    try:
        distribution(DISTRIBUTION)
    except PackageNotFoundError:
        return False
    return True


def _require_external_plugin() -> None:
    """Skip when the fixture is absent, unless the job declared it mandatory."""
    if _installed():
        return
    missing = (
        f"{DISTRIBUTION} is not installed in this environment; "
        f"install it with `pip install {FIXTURE_ROOT}` to exercise real "
        "entry-point discovery"
    )
    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(f"{REQUIRE_ENV}=1 demands a real plugin installation, but {missing}.")
    pytest.skip(missing)


@pytest.fixture
def check_result(tmp_path: Path) -> CheckResult:
    """A `CheckResult` from the real pipeline, not a hand-built stand-in."""
    (tmp_path / "README.md").write_text(
        "# Contract fixture\n\n## Reproducing results\n\n```bash\npython train.py\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "train.py").write_text(
        "import random\n\nrandom.seed(0)\nprint(random.random())\n", encoding="utf-8"
    )
    return run_check(tmp_path)


def test_the_installed_distribution_registers_both_public_groups() -> None:
    _require_external_plugin()

    rule_entries = [
        entry for entry in entry_points(group="adduce.rules") if entry.module == RULE_MODULE
    ]
    reporter_entries = [
        entry for entry in entry_points(group="adduce.reporters") if entry.module == REPORTER_MODULE
    ]

    assert [entry.name for entry in rule_entries] == [FORMAT_NAME]
    assert sorted(entry.name for entry in reporter_entries) == [FORMAT_NAME, SHADOWED_FORMAT]
    for entry in rule_entries + reporter_entries:
        assert entry.dist is not None, f"{entry.name} carries no distribution metadata"
        assert _canonical(entry.dist.name) == _canonical(DISTRIBUTION)


def test_rule_discovery_reaches_the_plugin_only_with_plugins_enabled() -> None:
    _require_external_plugin()

    with_plugins = discover_rules(include_plugins=True)
    without_plugins = discover_rules(include_plugins=False)

    discovered = [rule for rule in with_plugins if rule.id == RULE_ID]
    assert len(discovered) == 1, "the installed rule pack did not survive discovery"
    assert type(discovered[0]).__module__ == RULE_MODULE
    assert RULE_ID not in {rule.id for rule in without_plugins}


def test_the_discovered_rule_evaluates_to_a_finding_with_a_real_status(
    check_result: CheckResult,
) -> None:
    _require_external_plugin()

    rule = next(rule for rule in discover_rules() if rule.id == RULE_ID)
    finding = rule.evaluate(check_result.evidence)

    assert isinstance(finding, Finding)
    assert finding.rule_id == RULE_ID
    assert isinstance(finding.status, Status)
    assert finding.status is Status[finding.status.name]
    assert RULE_ID in {reported.rule_id for reported in check_result.card.findings}


def test_the_plugin_reporter_renders_a_real_check_result(check_result: CheckResult) -> None:
    _require_external_plugin()

    assert FORMAT_NAME in RENDERERS, f"available formats: {sorted(RENDERERS)}"
    renderer = RENDERERS[FORMAT_NAME]
    assert renderer.__module__ == REPORTER_MODULE

    rendered = renderer(check_result)
    assert isinstance(rendered, str)
    assert rendered.strip()


def test_a_builtin_format_name_cannot_be_shadowed(check_result: CheckResult) -> None:
    _require_external_plugin()

    shadow_attempts = [
        entry
        for entry in entry_points(group="adduce.reporters")
        if entry.name == SHADOWED_FORMAT and entry.module == REPORTER_MODULE
    ]
    assert shadow_attempts, "the fixture no longer attempts to shadow a built-in format"

    assert RENDERERS[SHADOWED_FORMAT] is json_report.render
    rendered = RENDERERS[SHADOWED_FORMAT](check_result)
    assert json.loads(rendered)["tool"]


# Structured child findings (docs/adr/0002-hierarchical-findings.md) are
# PROPOSED and unbuilt. Their contract test belongs here as a further test
# function over the fixtures above; nothing already written has to change.
