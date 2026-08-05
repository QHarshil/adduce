"""The benchmark harness: honest absences, correct strata, real regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bench import runner, worker

_STRATA: list[dict[str, Any]] = [
    {"id": "XS", "max_python_loc": 2000},
    {"id": "S", "max_python_loc": 20000},
    {"id": "M", "max_python_loc": 150000},
    {"id": "L", "max_python_loc": 1000000},
    {"id": "XL", "max_python_loc": None},
]

_FIXTURE = {
    "README.md": "# Demo\n\n## Reproducing results\n\n```bash\npython train.py\n```\n",
    "requirements.txt": "torch==2.1.0\n",
    "train.py": "import torch\n\ntorch.manual_seed(0)\n",
}


def _fixture(root: Path) -> Path:
    for relative, content in _FIXTURE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    return root


# -- strata -----------------------------------------------------------------


def test_strata_are_assigned_from_measured_loc() -> None:
    assert runner._stratum(0, _STRATA) == "XS"
    assert runner._stratum(2000, _STRATA) == "XS"
    assert runner._stratum(2001, _STRATA) == "S"
    assert runner._stratum(102930, _STRATA) == "M"
    assert runner._stratum(1687480, _STRATA) == "XL"


# -- honest absence ---------------------------------------------------------


def test_a_missing_target_is_reported_unavailable_with_a_reason(
    tmp_path: Path, capsys: Any
) -> None:
    assert worker.main(["--path", str(tmp_path / "absent")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert "absent" in payload["reason"]
    assert "performance" not in payload


def test_peak_rss_reports_its_unit_or_an_explicit_absence() -> None:
    observation = worker.peak_rss_observation()
    assert observation["platform"] == sys.platform
    if observation["available"]:
        assert observation["unit"] in {"bytes", "kibibytes"}
        assert isinstance(observation["value"], int) and observation["value"] > 0
    else:
        assert observation["value"] is None
        assert observation["unit"] == "unavailable"


# -- a real measurement -----------------------------------------------------


def test_measure_reports_real_work_on_a_fixture_repository(tmp_path: Path) -> None:
    measurement = worker.measure(_fixture(tmp_path), honor_gitignore=False)

    assert measurement["available"] is True
    assert measurement["inputs"]["files"] == len(_FIXTURE)
    assert measurement["inputs"]["python_files"] == 1
    assert measurement["inputs"]["python_loc"] == 3
    assert measurement["performance"]["cold_runtime_seconds"] > 0
    assert measurement["performance"]["warm_path_exists"] is False
    assert measurement["performance"]["stage_milliseconds"]["total"] > 0
    assert measurement["outcome"]["findings"] > 0
    assert measurement["determinism"]["repeat_render_byte_identical"] is True


def test_the_worker_subprocess_path_produces_a_usable_record(tmp_path: Path) -> None:
    record = runner._run_worker(_fixture(tmp_path), honor_gitignore=False)
    assert record["available"] is True
    assert record["inputs"]["python_files"] == 1


# -- finding diff -----------------------------------------------------------


def test_rule_statuses_are_absent_unless_the_finding_diff_asks_for_them(
    tmp_path: Path,
) -> None:
    """78 entries per arm would dominate a report meant to be read."""
    plain = worker.measure(_fixture(tmp_path), honor_gitignore=False)
    assert "rule_statuses" not in plain["outcome"]

    detailed = worker.measure(_fixture(tmp_path), honor_gitignore=False, rule_statuses=True)
    statuses = detailed["outcome"]["rule_statuses"]
    assert len(statuses) == detailed["outcome"]["findings"]
    assert all(isinstance(status, str) for status in statuses.values())


def test_a_rule_that_stops_applying_is_distinguished_from_one_that_changes_verdict() -> None:
    """Producing no finding is a different fact from reaching a new conclusion."""
    assert runner._classify_move("pass", None) == "stopped_applying"
    assert runner._classify_move(None, "pass") == "started_applying"
    assert runner._classify_move("pass", "not-applicable") == "became_not_applicable"
    assert runner._classify_move("fail", "not-applicable") == "became_not_applicable"
    assert runner._classify_move("pass", "fail") == "dropped"
    assert runner._classify_move("pass", "partial") == "dropped"
    assert runner._classify_move("partial", "fail") == "dropped"
    assert runner._classify_move("partial", "pass") == "improved"
    assert runner._classify_move("fail", "pass") == "improved"
    # Neither side scores, so calling it a drop or a gain would be an invention.
    assert runner._classify_move("unknown", "unknown") == "changed_scoring_eligibility"


def test_finding_diff_reports_no_movement_when_nothing_is_ignored(tmp_path: Path) -> None:
    """A repository with no ignored paths must show an empty diff, not an error."""
    target = _fixture(tmp_path / "repo")
    strata = tmp_path / "strata.json"
    strata.write_text(
        json.dumps(
            {
                "loc_strata": _STRATA,
                "targets": [
                    {
                        "id": "fixture",
                        "kind": "synthetic",
                        "path": str(target),
                        "framework": "torch",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = runner.finding_diff(strata)

    assert report["schema"] == "adduce-finding-diff/1"
    assert report["summary"]["targets_measured"] == 1
    assert report["summary"]["rules_moved_total"] == 0
    assert report["summary"]["targets_unchanged"] == 1
    assert report["results"][0]["moves"] == []
    # Rendering must not raise on an empty diff.
    assert "fixture" in runner._render_finding_diff(report)


def test_finding_diff_records_an_unavailable_target_with_its_reason(tmp_path: Path) -> None:
    strata = tmp_path / "strata.json"
    strata.write_text(
        json.dumps(
            {
                "loc_strata": _STRATA,
                "targets": [
                    {
                        "id": "absent",
                        "kind": "clone",
                        "path": str(tmp_path / "nope"),
                        "framework": "torch",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = runner.finding_diff(strata)

    record = report["results"][0]
    assert record["available"] is False
    assert record["reason"]
    assert report["summary"]["targets_unavailable"] == 1
    assert report["summary"]["rules_moved_total"] == 0


# -- regression detection ---------------------------------------------------


def _report(**overrides: Any) -> dict[str, Any]:
    default = {
        "available": True,
        "inputs": {"files": 10, "python_files": 2, "python_loc": 100, "bytes": 500},
        "performance": {
            "cold_runtime_seconds": 1.0,
            "disk_reads_per_inventoried_file": 1.0,
        },
        "outcome": {"score": 50.0, "tier": "Bronze", "findings": 78, "parser_failures": 0},
        "determinism": {"repeat_render_byte_identical": True},
    }
    for section, values in overrides.items():
        default[section] = {**default[section], **values}  # type: ignore[dict-item]
    return {
        "results": [
            {"id": "target", "kind": "synthetic", "path": "p", "default": default},
        ]
    }


def test_an_identical_report_shows_no_regression() -> None:
    baseline = _report()
    assert runner._regressions(baseline, _report()) == []


def test_losing_determinism_is_a_regression() -> None:
    problems = runner._regressions(
        _report(), _report(determinism={"repeat_render_byte_identical": False})
    )
    assert any("byte-identical" in problem for problem in problems)


def test_more_parser_failures_is_a_regression() -> None:
    problems = runner._regressions(
        _report(), _report(outcome={"parser_failures": 3, "score": 50.0})
    )
    assert any("parser failures rose" in problem for problem in problems)


def test_a_moved_synthetic_score_is_a_regression() -> None:
    problems = runner._regressions(_report(), _report(outcome={"score": 61.0}))
    assert any("synthetic-corpus score moved" in problem for problem in problems)


def test_slower_cold_runtime_beyond_tolerance_is_a_regression() -> None:
    within = runner._regressions(_report(), _report(performance={"cold_runtime_seconds": 1.2}))
    assert not any("cold runtime" in problem for problem in within)
    beyond = runner._regressions(_report(), _report(performance={"cold_runtime_seconds": 1.6}))
    assert any("cold runtime regressed" in problem for problem in beyond)


def test_more_disk_reads_per_file_is_a_regression() -> None:
    problems = runner._regressions(
        _report(), _report(performance={"disk_reads_per_inventoried_file": 2.47})
    )
    assert any("disk reads per file rose" in problem for problem in problems)


def test_a_target_absent_from_the_baseline_is_not_a_regression() -> None:
    """A new target must not fail the gate that introduces it."""
    baseline: dict[str, Any] = {"results": []}
    assert runner._regressions(baseline, _report()) == []


def test_a_different_input_set_is_not_comparable_rather_than_a_regression() -> None:
    """The normal case for a target whose gitignored content is absent in CI."""
    slower_but_smaller = _report(
        inputs={"files": 4, "python_loc": 20},
        performance={"cold_runtime_seconds": 99.0, "disk_reads_per_inventoried_file": 9.0},
        outcome={"score": 99.0, "parser_failures": 9},
    )
    assert runner._regressions(_report(), slower_but_smaller) == []


def test_determinism_is_still_checked_when_inputs_differ() -> None:
    broken = _report(
        inputs={"files": 4, "python_loc": 20},
        determinism={"repeat_render_byte_identical": False},
    )
    assert any("byte-identical" in problem for problem in runner._regressions(_report(), broken))


def test_an_unavailable_current_target_is_not_a_regression() -> None:
    """Clone targets are absent in CI; that must not fail the build."""
    current = {
        "results": [
            {
                "id": "target",
                "kind": "clone",
                "path": "p",
                "default": {"available": False, "reason": "path is not a directory"},
            }
        ]
    }
    assert runner._regressions(_report(), current) == []
