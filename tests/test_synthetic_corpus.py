"""Targeted synthetic controls for detector regression behaviour.

Each directory under corpus/synthetic/ isolates one behaviour (a real drift,
a rounding-level match, a red-team trap...) and corpus/synthetic/
expectations.yaml pins what adduce must and must not report for it. These cases
do not estimate real-world false-positive rates. Tune the fixtures, never the
expectations, unless the intended detector behaviour genuinely changed.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from adduce.engine import run_check
from adduce.report import RENDERERS

SYNTHETIC_DIR = Path(__file__).resolve().parent.parent / "corpus" / "synthetic"
CASES = sorted(p.name for p in SYNTHETIC_DIR.iterdir() if p.is_dir())

# Amazon's documented example access key; used by the synthetic_secret
# fixture and asserted to never leak into report output.
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _statuses(case: str) -> dict[str, str]:
    result = run_check(SYNTHETIC_DIR / case)
    return {f.rule_id: f.status.value for f in result.card.findings}


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a mapping key written twice.

    ``yaml.safe_load`` keeps the last of two equal keys and reports nothing, so
    a merge that leaves one case with two ``forbid`` blocks silently drops one.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


@cache
def _expectations() -> list[dict[str, Any]]:
    text = (SYNTHETIC_DIR / "expectations.yaml").read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = yaml.load(text, Loader=_UniqueKeyLoader)
    return entries


def test_a_key_written_twice_is_refused() -> None:
    document = "- case: one\n  forbid:\n    R-X: pass\n  forbid:\n    R-X: fail\n"
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate key 'forbid'"):
        yaml.load(document, Loader=_UniqueKeyLoader)


def test_expectations_write_no_key_twice() -> None:
    assert _expectations()


def test_every_case_directory_has_expectations() -> None:
    assert sorted(entry["case"] for entry in _expectations()) == CASES


def test_every_entry_expects_or_forbids_something() -> None:
    silent = [e["case"] for e in _expectations() if not (e.get("expect") or e.get("forbid"))]
    assert silent == []


@pytest.mark.parametrize("case", CASES)
def test_synthetic_case(case: str) -> None:
    entry = {e["case"]: e for e in _expectations()}[case]
    statuses = _statuses(case)
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
