"""Positive-control synthetic corpus: the permanent false-positive suite.

Each directory under corpus/synthetic/ isolates one behaviour (a real drift,
a rounding-level match, a red-team trap...) and corpus/synthetic/
expectations.yaml pins what adduce must and must not report for it. Tune the
fixtures, never the expectations, unless the tool's behaviour is genuinely
correct and the expectation wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from adduce.engine import run_check
from adduce.report import RENDERERS

SYNTHETIC_DIR = Path(__file__).resolve().parent.parent / "corpus" / "synthetic"
EXPECTATIONS = yaml.safe_load(
    (SYNTHETIC_DIR / "expectations.yaml").read_text(encoding="utf-8")
)

# Amazon's documented example access key; used by the synthetic_secret
# fixture and asserted to never leak into report output.
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _statuses(case: str) -> dict[str, str]:
    result = run_check(SYNTHETIC_DIR / case)
    return {f.rule_id: f.status.value for f in result.card.findings}


def test_every_case_directory_has_expectations() -> None:
    cases = {entry["case"] for entry in EXPECTATIONS}
    directories = {p.name for p in SYNTHETIC_DIR.iterdir() if p.is_dir()}
    assert cases == directories


@pytest.mark.parametrize("entry", EXPECTATIONS, ids=[e["case"] for e in EXPECTATIONS])
def test_synthetic_case(entry: dict) -> None:
    statuses = _statuses(entry["case"])
    for rule_id, wanted in (entry.get("expect") or {}).items():
        observed = statuses.get(rule_id)
        assert observed == wanted, (
            f"{entry['case']}: expected {rule_id}={wanted}, observed {observed}"
        )
    for rule_id, banned in (entry.get("forbid") or {}).items():
        observed = statuses.get(rule_id)
        assert observed != banned, (
            f"{entry['case']}: {rule_id} must not be {banned}, but it is"
        )


def test_secret_value_never_echoed_in_json_report() -> None:
    result = run_check(SYNTHETIC_DIR / "synthetic_secret")
    report = RENDERERS["json"](result)
    assert FAKE_AWS_KEY not in report
