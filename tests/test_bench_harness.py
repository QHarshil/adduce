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


def _single_target_strata(strata_path: Path, target_id: str, path: Path) -> None:
    strata_path.write_text(
        json.dumps(
            {
                "loc_strata": _STRATA,
                "targets": [
                    {"id": target_id, "kind": "synthetic", "path": str(path), "framework": "torch"}
                ],
            }
        ),
        encoding="utf-8",
    )


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


# -- a real measurement -------------------------------------------------------


def test_measure_reports_real_work_on_a_fixture_repository(tmp_path: Path) -> None:
    """The default mode is one ``run_check`` call: no determinism block at all."""
    measurement = worker.measure(_fixture(tmp_path), honor_gitignore=False)

    assert measurement["available"] is True
    assert measurement["inputs"]["files"] == len(_FIXTURE)
    assert measurement["inputs"]["python_files"] == 1
    assert measurement["inputs"]["python_loc"] == 3
    assert measurement["performance"]["cold_runtime_seconds"] > 0
    assert measurement["performance"]["stage_milliseconds"]["total"] > 0
    assert measurement["outcome"]["findings"] > 0
    assert "determinism" not in measurement
    assert "warm_path_exists" not in measurement["performance"]
    assert "repeat_runtime_seconds" not in measurement["performance"]


def test_measure_determinism_reports_only_the_determinism_block(tmp_path: Path) -> None:
    """``--determinism`` runs two analyses and reports nothing else."""
    measurement = worker.measure(_fixture(tmp_path), honor_gitignore=False, determinism=True)

    assert measurement["available"] is True
    assert "inputs" not in measurement
    assert "performance" not in measurement
    assert "outcome" not in measurement
    determinism = measurement["determinism"]
    assert determinism["repeat_render_byte_identical"] is True
    assert determinism["repeat_runtime_seconds"] > 0
    assert determinism["warm_path_exists"] is False


def test_the_worker_subprocess_path_produces_a_usable_record(tmp_path: Path) -> None:
    record = runner._run_worker(_fixture(tmp_path), honor_gitignore=False)
    assert record["available"] is True
    assert record["inputs"]["python_files"] == 1


def test_run_worker_command_carries_the_src_and_determinism_flags(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict[str, Any] = {}

    class _Result:
        returncode = 0
        stdout = json.dumps({"available": True})
        stderr = ""

    def fake_run(command: list[str], **kwargs: Any) -> _Result:
        captured["command"] = command
        return _Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    alt_src = tmp_path / "alt-src"
    runner._run_worker(tmp_path, honor_gitignore=True, src=alt_src, determinism=True)

    command = captured["command"]
    assert "--gitignore" in command
    assert "--determinism" in command
    assert command[command.index("--src") + 1] == str(alt_src)


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
    _single_target_strata(strata, "fixture", target)

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
    _single_target_strata(strata, "absent", tmp_path / "nope")

    report = runner.finding_diff(strata)

    record = report["results"][0]
    assert record["available"] is False
    assert record["reason"]
    assert report["summary"]["targets_unavailable"] == 1
    assert report["summary"]["rules_moved_total"] == 0


# -- median / spread over reps ------------------------------------------------


def test_runtime_statistics_computes_median_spread_and_a_representative_index() -> None:
    """Measured, on this machine: three reps of identical code, 17.844-20.188s."""
    median, spread, index = runner._runtime_statistics([17.844, 19.970, 20.188])
    assert median == 19.97
    assert spread == round((20.188 - 17.844) / 19.970, 4)
    # 19.970s is the second rep by call order; that is the one that travels.
    assert index == 1


def test_runtime_statistics_handles_a_single_sample() -> None:
    median, spread, index = runner._runtime_statistics([2.5])
    assert median == 2.5
    assert spread == 0.0
    assert index == 0


def _fake_performance_worker(runtimes: list[float], calls: list[int]) -> Any:
    """A ``_run_worker`` stand-in returning canned per-rep runtimes, in order."""

    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        if determinism:
            return {
                "available": True,
                "adduce_version": "0.0.0",
                "honor_gitignore": honor_gitignore,
                "determinism": {
                    "repeat_render_byte_identical": True,
                    "comparison": "two run_check calls in one process, default JSON report",
                    "repeat_runtime_seconds": 0.01,
                    "warm_path_exists": False,
                },
            }
        index = len(calls)
        calls.append(index)
        return {
            "available": True,
            "adduce_version": "0.0.0",
            "honor_gitignore": honor_gitignore,
            "inputs": {"files": 3, "python_files": 1, "python_loc": 3, "bytes": 10},
            "performance": {
                "cold_runtime_seconds": runtimes[index],
                "peak_rss": {"available": True, "value": 1000 + index, "unit": "bytes"},
            },
            "outcome": {"score": 50.0, "tier": "Bronze", "findings": 1, "parser_failures": 0},
        }

    return fake_run_worker


def test_measure_arm_reduces_reps_to_a_median_with_one_representative_sample(
    monkeypatch: Any,
) -> None:
    runtimes = [17.844, 20.188, 19.970]
    calls: list[int] = []
    monkeypatch.setattr(runner, "_run_worker", _fake_performance_worker(runtimes, calls))

    arm = runner._measure_arm(Path("unused"), honor_gitignore=True, reps=3)

    assert len(calls) == 3
    assert arm["performance"]["cold_runtime_seconds"] == 19.97
    assert arm["performance"]["cold_runtime_samples"] == runtimes
    assert arm["performance"]["cold_runtime_spread"] == round((20.188 - 17.844) / 19.97, 4)
    # The median rep (19.970s) is the third call (index 2); its peak_rss travels.
    assert arm["performance"]["peak_rss"]["value"] == 1002
    assert arm["determinism"]["repeat_render_byte_identical"] is True


def test_measure_arm_is_unavailable_if_any_performance_rep_fails(monkeypatch: Any) -> None:
    def fake_run_worker(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"available": False, "reason": "boom"}

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    arm = runner._measure_arm(Path("unused"), honor_gitignore=True, reps=3)
    assert arm == {"available": False, "reason": "boom"}


def test_measure_arm_is_unavailable_if_the_determinism_call_fails(monkeypatch: Any) -> None:
    """A failed determinism check must not be reported as a smaller, silent success."""

    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        if determinism:
            return {"available": False, "reason": "determinism boom"}
        return {
            "available": True,
            "adduce_version": "0.0.0",
            "honor_gitignore": honor_gitignore,
            "inputs": {"files": 3, "python_files": 1, "python_loc": 3, "bytes": 10},
            "performance": {"cold_runtime_seconds": 0.05},
            "outcome": {"score": 50.0, "tier": "Bronze", "findings": 1, "parser_failures": 0},
        }

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    arm = runner._measure_arm(Path("unused"), honor_gitignore=True, reps=2)
    assert arm == {"available": False, "reason": "determinism boom"}


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


def test_cold_runtime_gate_ignores_the_drift_between_two_reports() -> None:
    """2.7x between-run drift was measured on one laptop; it must not fire.

    A report is compared against a baseline taken at another time, often on
    another machine state. Anything short of a gross failure is indistinguishable
    from that drift, and `ab` is what resolves a real effect.
    """
    for slower in (1.2, 1.8, 2.7, 3.9):
        problems = runner._regressions(
            _report(), _report(performance={"cold_runtime_seconds": slower})
        )
        assert not any("cold runtime" in problem for problem in problems), slower


def test_recorded_spread_does_not_relax_the_cold_runtime_gate() -> None:
    """Spread is within-run; the gate answers a between-run question.

    Recording a tight spread must not be read as confidence about the absolute
    level. The contaminated baseline that motivated this had three reps agreeing
    to 6.1% at a value 2.7x its own quiet-machine figure.
    """
    baseline = _report(performance={"cold_runtime_spread": 0.2})
    current = _report(performance={"cold_runtime_seconds": 1.3, "cold_runtime_spread": 0.2})
    assert not any("cold runtime" in p for p in runner._regressions(baseline, current))

    tight = _report(performance={"cold_runtime_spread": 0.001})
    tight_current = _report(
        performance={"cold_runtime_seconds": 1.3, "cold_runtime_spread": 0.001}
    )
    assert not any("cold runtime" in p for p in runner._regressions(tight, tight_current))


def test_a_gross_cold_runtime_failure_still_fires() -> None:
    """Past 4x is no longer explicable as drift, so it is worth saying."""
    problems = runner._regressions(
        _report(), _report(performance={"cold_runtime_seconds": 4.5})
    )
    assert any("cold runtime" in problem for problem in problems)
    # and it must not be quotable as a measured effect size
    assert any("confirm with `ab`" in problem for problem in problems)


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


# -- ab: paired, interleaved comparison --------------------------------------


def test_ab_delta_is_not_resolvable_when_the_reps_disagree_in_sign() -> None:
    """Reps that move both ways are noise, however large the median looks."""
    delta, resolvable, per_rep = runner._ab_delta([1.0, 1.0, 1.0, 1.0], [1.2, 0.8, 1.3, 0.7])
    assert per_rep == [0.2, -0.2, 0.3, -0.3]
    assert resolvable is False


def test_ab_delta_is_resolvable_when_every_rep_moves_the_same_way() -> None:
    delta, resolvable, per_rep = runner._ab_delta([1.0, 1.0, 1.0, 1.0], [0.9, 0.95, 0.92, 0.97])
    assert delta == -0.065
    assert resolvable is True


def test_ab_delta_survives_drift_that_swamps_the_effect() -> None:
    """The case the aggregate-spread form got wrong, from real measurements.

    Both arms drift upward across the run by more than the effect, so each arm's
    own spread exceeds the difference between them. Pairwise, every rep still
    moved the same way, which is the whole reason the arms are interleaved.
    """
    baseline = [2.0065, 2.0644, 2.0417, 2.0475, 2.1257, 2.1329]
    current = [1.8305, 1.9858, 1.9280, 1.9524, 1.9574, 1.9639]

    _, baseline_spread, _ = runner._runtime_statistics(baseline)
    _, current_spread, _ = runner._runtime_statistics(current)
    delta, resolvable, per_rep = runner._ab_delta(baseline, current)

    assert abs(delta) < baseline_spread + current_spread  # the old form abstained here
    assert all(d < 0 for d in per_rep)
    assert resolvable is True


def test_ab_delta_needs_more_than_one_rep() -> None:
    """One pair agrees with itself trivially; that is not a sign test."""
    _, resolvable, _ = runner._ab_delta([1.0], [0.5])
    assert resolvable is False


def test_ab_alternates_the_two_arms_within_each_rep(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[Path | None] = []

    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        calls.append(src)
        return {"available": True, "performance": {"cold_runtime_seconds": 1.0}}

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    strata = tmp_path / "strata.json"
    _single_target_strata(strata, "fixture", _fixture(tmp_path / "repo"))
    baseline_src, current_src = tmp_path / "baseline-src", tmp_path / "current-src"

    runner.ab(strata, baseline_src=baseline_src, current_src=current_src, reps=4)

    # Adjacent within a rep, and which arm leads flips every rep, so no arm
    # carries the cost of going first in every pair.
    assert calls == [
        baseline_src,
        current_src,
        current_src,
        baseline_src,
        baseline_src,
        current_src,
        current_src,
        baseline_src,
    ]


def test_ab_reports_a_same_tree_self_test_as_not_resolvable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The self-test the acceptance criteria names: identical trees, no claim."""

    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        return {"available": True, "performance": {"cold_runtime_seconds": 1.0}}

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    strata = tmp_path / "strata.json"
    _single_target_strata(strata, "fixture", _fixture(tmp_path / "repo"))

    report = runner.ab(
        strata, baseline_src=tmp_path / "src", current_src=tmp_path / "src", reps=3
    )

    record = report["results"][0]
    assert record["available"] is True
    assert record["delta"] == 0.0
    assert record["resolvable"] is False
    assert report["summary"]["resolvable_deltas"] == 0
    # The whole point of the subcommand: print it as not resolvable, not as a result.
    assert "not resolvable" in runner._render_ab(report)


def test_ab_reports_a_real_difference_as_resolvable(tmp_path: Path, monkeypatch: Any) -> None:
    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        seconds = 1.0 if src is not None and src.name == "baseline-src" else 2.0
        return {"available": True, "performance": {"cold_runtime_seconds": seconds}}

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    strata = tmp_path / "strata.json"
    _single_target_strata(strata, "fixture", _fixture(tmp_path / "repo"))

    report = runner.ab(
        strata,
        baseline_src=tmp_path / "baseline-src",
        current_src=tmp_path / "current-src",
        reps=3,
    )

    record = report["results"][0]
    assert record["resolvable"] is True
    assert record["delta"] == 1.0
    assert report["summary"]["resolvable_deltas"] == 1


def test_ab_records_a_mid_run_failure_with_its_reason(tmp_path: Path, monkeypatch: Any) -> None:
    """A failure partway through must void the target, not report a shorter one."""
    call_count = 0

    def fake_run_worker(
        path: Path,
        *,
        honor_gitignore: bool,
        rule_statuses: bool = False,
        src: Path | None = None,
        determinism: bool = False,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            return {"available": False, "reason": "worker exited 1"}
        return {"available": True, "performance": {"cold_runtime_seconds": 1.0}}

    monkeypatch.setattr(runner, "_run_worker", fake_run_worker)

    strata = tmp_path / "strata.json"
    _single_target_strata(strata, "fixture", _fixture(tmp_path / "repo"))

    report = runner.ab(strata, baseline_src=tmp_path / "b", current_src=tmp_path / "c", reps=3)

    record = report["results"][0]
    assert record["available"] is False
    assert record["reason"] == "worker exited 1"
    assert report["summary"]["targets_unavailable"] == 1
