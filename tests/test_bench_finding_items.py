"""The finding-item benchmark: present measurements, refused garbage, honest absence.

These tests exercise the harness, not the benchmark: every size here is tiny.
A test that re-ran the real 10k/50k/100k measurement would take a minute and
would still not be measuring anything, because the suite discards the report.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from bench import finding_items

_TINY = 25
_REPS = 2

_EXPECTED_METRICS = {
    "construction_items_seconds",
    "construction_parent_seconds",
    "construction_total_seconds",
    "to_dict_seconds",
    "json_dumps_seconds",
    "summarize_items_seconds",
    "json_report_seconds",
    "sarif_render_seconds",
    "markdown_render_seconds",
    "terminal_render_seconds",
    "terminal_verbose_render_seconds",
    "items_json_bytes",
    "finding_json_bytes",
    "report_json_bytes",
    "sarif_json_bytes",
    "markdown_bytes",
    "traced_retained_bytes",
    "traced_peak_bytes",
}


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    """One real measurement at a tiny size, shared by the tests that read it."""
    return finding_items.measure([_TINY], reps=_REPS)


def _metric_record(record: dict[str, Any], size: int) -> dict[str, Any]:
    return {
        "size": size,
        "reps": 1,
        "available": True,
        "metrics": {name: dict(record, unit="seconds") for name in ("cost_seconds",)},
    }


# -- report structure ---------------------------------------------------------


def test_the_report_carries_its_schema_and_the_same_provenance_runner_records(
    report: dict[str, Any],
) -> None:
    assert report["schema"] == "adduce-bench-finding-items/1"
    provenance = report["provenance"]
    for key in (
        "generated_at",
        "adduce_version",
        "git_commit",
        "git_dirty",
        "python",
        "platform",
        "machine",
        "load_average",
    ):
        assert key in provenance, key
    assert provenance["python"].count(".") == 2
    assert report["reps"] == _REPS
    assert report["sizes"] == [_TINY]
    assert set(report["notes"]) == {
        "allocation_pass",
        "resident",
        "isolation",
        "warmup",
        "interleaving",
        "growth_flag",
        "check_result",
    }


def test_the_default_sizes_are_the_three_the_contract_asks_about() -> None:
    assert finding_items.parse_sizes(finding_items.DEFAULT_SIZES) == (10000, 50000, 100000)


def test_every_measurement_is_present_sane_and_names_its_unit(report: dict[str, Any]) -> None:
    (record,) = report["results"]
    assert record["size"] == _TINY
    assert record["reps"] == _REPS
    assert record["available"] is True
    assert set(record["metrics"]) == _EXPECTED_METRICS
    for name, metric in record["metrics"].items():
        assert metric["unit"] == ("seconds" if name.endswith("_seconds") else "bytes"), name
        assert len(metric["samples"]) == _REPS, name
        assert metric["min"] <= metric["median"] <= metric["max"], name
        assert metric["median"] > 0, name
        assert metric["per_item"] == pytest.approx(metric["median"] / _TINY), name
        assert metric["spread"] >= 0.0, name


def test_a_metric_name_that_does_not_state_its_unit_is_refused() -> None:
    with pytest.raises(ValueError, match="does not name its unit"):
        finding_items._unit("mystery_cost")


# -- what was generated -------------------------------------------------------


def test_the_report_states_exactly_what_an_item_looked_like(report: dict[str, Any]) -> None:
    shape = report["item_shape"]
    example = shape["example"]
    assert shape["generator"] == "bench.finding_items.make_item"
    assert shape["attributes_per_item"] == len(example["attributes"]) == 3
    assert shape["example_json_bytes"] == len(json.dumps(example).encode("utf-8"))
    assert example["id"].startswith("claim:table-")
    assert example["kind"] == "numeric-claim"
    assert 60 <= len(example["message"]) <= 160
    assert shape["parent"]["status"] == "fail"


def test_items_are_unique_deterministic_and_carry_a_location_on_every_third() -> None:
    first = finding_items.make_items(90)
    assert len({item.id for item in first}) == 90
    assert [item.to_dict() for item in first] == [
        item.to_dict() for item in finding_items.make_items(90)
    ]
    with_locations = [index for index, item in enumerate(first) if item.locations]
    assert with_locations == list(range(0, 90, finding_items._LOCATION_EVERY))
    assert {item.status.value for item in first} == {"pass", "fail", "partial"}


def test_the_reported_json_sizes_are_the_real_serialised_sizes(report: dict[str, Any]) -> None:
    """A size the harness could have estimated is recomputed here instead."""
    finding = finding_items.make_finding(finding_items.make_items(_TINY))
    payload = finding.to_dict()
    metrics = report["results"][0]["metrics"]
    assert metrics["items_json_bytes"]["median"] == len(
        json.dumps(payload["items"]).encode("utf-8")
    )
    assert metrics["finding_json_bytes"]["median"] == len(json.dumps(payload).encode("utf-8"))
    assert metrics["sarif_json_bytes"]["median"] > metrics["report_json_bytes"]["median"]


# -- resident memory ---------------------------------------------------------


def test_resident_growth_is_measured_in_its_own_process_and_names_its_unit(
    report: dict[str, Any],
) -> None:
    resident = report["results"][0]["resident"]
    if not resident["available"]:
        pytest.fail(f"resident growth unmeasured: {resident.get('reason')}")
    assert resident["unit"] in {"bytes", "kibibytes"}
    assert "subprocess" in resident["source"]
    assert resident["reps"] == _REPS
    for key in ("baseline", "items_growth", "pass_peak_growth"):
        assert resident[key]["median"] >= 0, key
        assert len(resident[key]["samples"]) == _REPS, key


def test_the_probe_readings_only_ever_grow() -> None:
    probe = finding_items.rss_probe(2)
    if not probe["available"]:
        assert probe["unit"] == "unavailable"
        assert "unavailable on" in probe["reason"]
        return
    readings = probe["readings"]
    ordered = [
        readings["baseline"],
        readings["after_fixture"],
        readings["after_items"],
        readings["after_pass"],
    ]
    assert ordered == sorted(ordered)
    assert probe["items_growth"] == readings["after_items"] - readings["after_fixture"]
    assert probe["pass_peak_growth"] == readings["after_pass"] - readings["after_fixture"]


def test_a_probe_that_cannot_start_is_recorded_unavailable_with_its_reason(
    monkeypatch: Any,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("no fork for you")

    monkeypatch.setattr(finding_items.subprocess, "run", refuse)
    resident = finding_items._measure_resident(_TINY, reps=_REPS)
    assert resident["available"] is False
    assert "no fork for you" in resident["reason"]
    assert "items_growth" not in resident


def test_a_probe_that_emits_nonsense_is_not_read_as_a_measurement(monkeypatch: Any) -> None:
    class _Completed:
        returncode = 0
        stdout = "not json"
        stderr = ""

    monkeypatch.setattr(finding_items.subprocess, "run", lambda *a, **k: _Completed())
    resident = finding_items._measure_resident(_TINY, reps=1)
    assert resident["available"] is False
    assert "invalid JSON" in resident["reason"]


# -- growth screening --------------------------------------------------------


def test_per_item_growth_beyond_the_threshold_is_flagged() -> None:
    linear = _metric_record({"median": 1.0, "per_item": 1e-4}, 10000)
    quadratic = _metric_record({"median": 100.0, "per_item": 1e-3}, 100000)
    scaling = finding_items._scaling([quadratic, linear])
    assert scaling["comparable"] is True
    assert scaling["worse_than_linear"] == ["cost_seconds"]
    assert scaling["metrics"]["cost_seconds"] == {
        "from_size": 10000,
        "to_size": 100000,
        "per_item_ratio": pytest.approx(10.0),
        "worse_than_linear": True,
    }


def test_an_unmeasured_size_is_left_out_of_the_growth_comparison() -> None:
    linear = _metric_record({"median": 1.0, "per_item": 1e-4}, 10000)
    absent = {"size": 100000, "reps": 1, "available": False, "reason": "worker exited 1"}
    scaling = finding_items._scaling([linear, absent])
    assert scaling["comparable"] is False
    assert scaling["metrics"] == {}


def test_a_linear_cost_is_not_flagged() -> None:
    scaling = finding_items._scaling(
        [
            _metric_record({"median": 1.0, "per_item": 1e-4}, 10000),
            _metric_record({"median": 10.4, "per_item": 1.04e-4}, 100000),
        ]
    )
    assert scaling["worse_than_linear"] == []


def test_a_single_size_reports_that_growth_cannot_be_assessed(report: dict[str, Any]) -> None:
    scaling = report["scaling"]
    assert scaling["comparable"] is False
    assert scaling["reason"] == "growth needs at least two measured sizes"
    assert scaling["metrics"] == {}


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize("text", ["10,20", " 10 , 20 ", "10,20,"])
def test_parse_sizes_accepts_a_comma_separated_list(text: str) -> None:
    assert finding_items.parse_sizes(text) == (10, 20)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no sizes"),
        (",", "no sizes"),
        ("abc", "'abc' is not an integer"),
        ("10,x", "'x' is not an integer"),
        ("1.5", "'1.5' is not an integer"),
        ("1e4", "'1e4' is not an integer"),
    ],
)
def test_parse_sizes_names_the_token_it_refuses(text: str, message: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=message):
        finding_items.parse_sizes(text)


@pytest.mark.parametrize("text", ["0", "-5", "10,0"])
def test_a_zero_or_negative_size_is_refused_rather_than_divided_by(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="is not positive"):
        finding_items.parse_sizes(text)


@pytest.mark.parametrize("size", [0, -1])
def test_measure_refuses_a_non_positive_size_before_measuring_anything(size: int) -> None:
    with pytest.raises(ValueError, match="size must be at least 1"):
        finding_items.measure([size], reps=1)
    with pytest.raises(ValueError, match="size must be at least 1"):
        finding_items.measure([10, size], reps=1)


def test_measure_refuses_an_empty_size_list() -> None:
    with pytest.raises(ValueError, match="no sizes to measure"):
        finding_items.measure([], reps=1)


@pytest.mark.parametrize("reps", [0, -3])
def test_measure_refuses_fewer_than_one_rep(reps: int) -> None:
    with pytest.raises(ValueError, match="reps must be at least 1"):
        finding_items.measure([_TINY], reps=reps)


@pytest.mark.parametrize(
    "argv",
    [["--sizes", "0"], ["--sizes", "abc"], ["--sizes", ""], ["--reps", "0", "--sizes", "5"]],
)
def test_the_cli_exits_two_without_measuring_when_an_argument_is_refused(
    argv: list[str], capsys: Any
) -> None:
    with pytest.raises(SystemExit) as raised:
        finding_items.main(argv)
    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


# -- the command line --------------------------------------------------------


def test_the_cli_writes_a_json_report_and_prints_a_table(tmp_path: Path, capsys: Any) -> None:
    output = tmp_path / "reports" / "items.json"
    assert finding_items.main(["--sizes", "20,40", "--reps", "1", "--output", str(output)]) == 0

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["schema"] == "adduce-bench-finding-items/1"
    assert [record["size"] for record in written["results"]] == [20, 40]
    assert written["scaling"]["comparable"] is True

    printed = capsys.readouterr()
    assert "20 items" in printed.out and "40 items" in printed.out
    assert "construction_total_seconds" in printed.out
    assert "us/item" in printed.out
    assert str(output) in printed.err


def test_the_rss_probe_mode_prints_one_json_object_and_measures_nothing_else(
    capsys: Any,
) -> None:
    assert finding_items.main(["--rss-probe", "5"]) == 0
    probe = json.loads(capsys.readouterr().out)
    assert probe["size"] == 5
    assert "readings" in probe


@pytest.mark.parametrize("flag", ["--rss-probe", "--measure-once"])
@pytest.mark.parametrize("size", ["0", "-2"])
def test_a_worker_mode_refuses_a_non_positive_size(flag: str, size: str) -> None:
    with pytest.raises(SystemExit) as raised:
        finding_items.main([flag, size])
    assert raised.value.code == 2


def test_the_timing_worker_mode_measures_one_pass_and_prints_it(capsys: Any) -> None:
    assert finding_items.main(["--measure-once", "5"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["available"] is True
    assert record["size"] == 5
    assert set(record["samples"]) == _EXPECTED_METRICS


def test_one_pass_measures_every_metric_the_report_carries() -> None:
    record = finding_items.worker_pass(4)
    assert set(record["samples"]) == _EXPECTED_METRICS
    assert all(value > 0 for value in record["samples"].values())


def test_a_size_whose_worker_fails_carries_no_invented_measurement(monkeypatch: Any) -> None:
    class _Failed:
        returncode = 3
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(finding_items.subprocess, "run", lambda *a, **k: _Failed())
    report = finding_items.measure([5], reps=2)
    (record,) = report["results"]
    assert record["available"] is False
    assert record["reason"] == "--measure-once exited 3"
    assert record["stderr_tail"] == "boom"
    assert "metrics" not in record and "resident" not in record
    assert report["scaling"]["comparable"] is False
    assert "5 items: unmeasured: --measure-once exited 3" in finding_items._render(report)


def test_an_unmeasured_resident_block_is_rendered_as_such_not_as_a_zero(
    report: dict[str, Any],
) -> None:
    broken = json.loads(json.dumps(report))
    broken["results"][0]["resident"] = {"available": False, "reason": "probe exited 1"}
    rendered = finding_items._render(broken)
    assert "resident unmeasured: probe exited 1" in rendered
    assert "0 bytes/item" not in rendered
