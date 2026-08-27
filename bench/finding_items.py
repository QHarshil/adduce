#!/usr/bin/env python3
"""What a finding's children cost, measured at 10k, 50k and 100k items.

0.2 promises that a rule may hang thousands of child observations off one
finding: 10,000 is the guaranteed envelope, aggregation stays linear, and the
machine-readable formats never silently truncate. A resource ceiling for that
promise has to come from measurement, so this builds one parent finding at each
requested size and measures construction, serialisation, aggregation, and every
reporter that walks the children -- including SARIF, which drops passes but
carries every child of every finding it reports, and is therefore the other
unbounded path for the failing finding measured here.

Deliberately not a ``bench/runner.py`` subcommand. ``runner`` measures
repository scans against strata, targets and a committed baseline; this measures
object construction and serialisation, with no repository involved beyond the
one throwaway fixture needed to obtain a ``CheckResult`` the real reporters
accept. Folding them together would distort both report shapes, and this stays
outside the CI benchmark regression gate, which compares scans against
``bench/reports/baseline.json``.

Nothing here estimates. Every timing is a median over ``--reps`` with the
observed spread, allocation is accounted by ``tracemalloc`` in its own pass, and
process peak RSS is reported apart from it because the two are different
quantities.
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_BENCH_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _BENCH_ROOT.parent
for _entry in (str(_REPOSITORY_ROOT), str(_REPOSITORY_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from rich.console import Console  # noqa: E402

from adduce.engine import CheckResult, run_check  # noqa: E402
from adduce.profiles import load_profile  # noqa: E402
from adduce.report.json_report import render as render_json  # noqa: E402
from adduce.report.markdown import render as render_markdown  # noqa: E402
from adduce.report.sarif import render as render_sarif  # noqa: E402
from adduce.report.terminal import render as render_terminal  # noqa: E402
from adduce.rules.base import (  # noqa: E402
    Category,
    Finding,
    FindingItem,
    Location,
    Status,
    summarize_items,
)
from adduce.safe_write import replace_text_regular  # noqa: E402
from adduce.scoring import score  # noqa: E402
from bench import runner, worker  # noqa: E402

REPORT_SCHEMA = "adduce-bench-finding-items/1"
DEFAULT_SIZES = "10000,50000,100000"
DEFAULT_REPS = 3

#: Per-item cost at the largest size, over per-item cost at the smallest, above
#: which the harness calls a metric worse than linear. Not a tolerance for
#: noise in a single metric -- it is deliberately loose, because a 10x change in
#: item count would move a genuinely quadratic cost by 10x, far past this.
PER_ITEM_GROWTH_THRESHOLD = 1.25

#: Items in the discarded pass that runs before anything is recorded. Without it
#: the first size measured also pays for every code path's first execution and
#: rich's console setup, which lands entirely in the smallest size and makes the
#: per-item ratios read as if later sizes were cheaper.
WARMUP_ITEMS = 1000

_MODULE_PATH = Path(__file__).resolve()
_WORKER_TIMEOUT_SECONDS = 900
_RULE_ID = "R-BENCH-ITEMS"
_ROWS_PER_TABLE = 25
_LOCATION_EVERY = 3
#: Seven passes, two fails, one partial: a reconciliation rule reports mostly
#: agreement, and a census of statuses that were all identical would not
#: exercise ``summarize_items``.
_STATUS_CYCLE = (
    Status.PASS,
    Status.PASS,
    Status.PASS,
    Status.PASS,
    Status.PASS,
    Status.PASS,
    Status.PASS,
    Status.FAIL,
    Status.FAIL,
    Status.PARTIAL,
)

_FIXTURE = {
    "README.md": "# Bench\n\n## Reproducing results\n\n```bash\npython train.py\n```\n",
    "requirements.txt": "torch==2.1.0\n",
    "train.py": "import torch\n\ntorch.manual_seed(0)\n",
}


def make_item(index: int) -> FindingItem:
    """One plausible child, derived entirely from ``index``.

    A domain-style id, a sentence of the length a reconciliation item really
    carries, three scalar attributes, and a location on every third item. No
    randomness, so a size is reproducible byte for byte. Deliberately no giant
    strings: how a pathological item body scales is a different question from
    how item *count* scales, and mixing them would make neither answerable.
    """
    status = _STATUS_CYCLE[index % len(_STATUS_CYCLE)]
    table, row = divmod(index, _ROWS_PER_TABLE)
    source = f"results/metrics-{index % 64}.json"
    paper_value = round(90.0 + (index % 1000) / 100.0, 2)
    artifact_value = round(paper_value - (0.37 if status is Status.PASS else 0.0), 2)
    return FindingItem(
        id=f"claim:table-{table + 1}:row-{row + 1}:accuracy",
        status=status,
        message=(
            f"Table {table + 1} row {row + 1} reports accuracy {paper_value:.2f}%; "
            f"{source} records {artifact_value:.2f}%."
        ),
        confidence=0.95 if status is Status.PASS else 0.6,
        locations=(
            (Location(source, index % 500 + 1),) if index % _LOCATION_EVERY == 0 else ()
        ),
        remediation="" if status is Status.PASS else "Re-run the evaluation or correct the table.",
        kind="numeric-claim",
        attributes={
            "paper_value": paper_value,
            "artifact_value": artifact_value,
            "source": source,
        },
    )


def make_items(size: int) -> list[FindingItem]:
    return [make_item(index) for index in range(size)]


def make_finding(items: Sequence[FindingItem]) -> Finding:
    """The parent. Failing, so SARIF -- which drops passes -- carries the children."""
    return Finding(
        rule_id=_RULE_ID,
        category=Category.DRIFT,
        title="Reported numbers agree with the recorded run",
        status=Status.FAIL,
        confidence=0.6,
        message="Some reported numbers disagree with the recorded run.",
        remediation="Reconcile each disagreeing row with its recorded value.",
        weight=5,
        severity="high",
        items=tuple(items),
    )


def item_shape() -> dict[str, Any]:
    """Exactly what was generated, because every size in the report depends on it."""
    example = make_item(0).to_dict()
    return {
        "generator": "bench.finding_items.make_item",
        "status_cycle": [status.value for status in _STATUS_CYCLE],
        "locations_on_every_nth_item": _LOCATION_EVERY,
        "attributes_per_item": len(example["attributes"]),
        "example": example,
        "example_json_bytes": len(json.dumps(example).encode("utf-8")),
        "parent": {
            "rule_id": _RULE_ID,
            "status": make_finding(()).status.value,
            "count": 1,
        },
    }


@contextmanager
def _fixture_result() -> Iterator[CheckResult]:
    """One real ``CheckResult`` for the reporters to render.

    Every reporter reads the repository, the configuration and the claim graph,
    so a hand-built stand-in would measure something the product never renders.
    The fixture is three files, which keeps the repository's own contribution
    constant: the only thing that differs between two sizes is the children.
    """
    root = Path(tempfile.mkdtemp(prefix="adduce-bench-items-")).resolve()
    try:
        for relative, content in _FIXTURE.items():
            (root / relative).write_text(content, encoding="utf-8")
        yield run_check(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _console() -> Console:
    return Console(file=io.StringIO(), width=100, force_terminal=False, legacy_windows=False)


def _measure_once(size: int, result: CheckResult) -> dict[str, float]:
    """One full pass at ``size``. Timings only; allocation is a separate pass.

    Each rendered artifact is sized and released before the next is built.
    Holding all of them to size them together would put a 35 MiB finding, a
    51 MiB report and a 60 MiB SARIF document in memory at once and report a
    process peak that no single ``adduce check`` invocation pays -- a harness
    artifact, not a product cost.
    """
    measurement: dict[str, float] = {}

    started = time.perf_counter()
    items = make_items(size)
    measurement["construction_items_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    finding = make_finding(items)
    measurement["construction_parent_seconds"] = time.perf_counter() - started
    measurement["construction_total_seconds"] = (
        measurement["construction_items_seconds"] + measurement["construction_parent_seconds"]
    )

    result.card = score([finding], load_profile("default"))

    started = time.perf_counter()
    payload = finding.to_dict()
    measurement["to_dict_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    finding_json = json.dumps(payload)
    measurement["json_dumps_seconds"] = time.perf_counter() - started
    measurement["finding_json_bytes"] = len(finding_json.encode("utf-8"))
    del finding_json
    measurement["items_json_bytes"] = len(json.dumps(payload["items"]).encode("utf-8"))
    del payload

    started = time.perf_counter()
    summarize_items(finding.items)
    measurement["summarize_items_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    report_json = render_json(result)
    measurement["json_report_seconds"] = time.perf_counter() - started
    measurement["report_json_bytes"] = len(report_json.encode("utf-8"))
    del report_json

    started = time.perf_counter()
    sarif_json = render_sarif(result)
    measurement["sarif_render_seconds"] = time.perf_counter() - started
    measurement["sarif_json_bytes"] = len(sarif_json.encode("utf-8"))
    del sarif_json

    started = time.perf_counter()
    markdown_text = render_markdown(result)
    measurement["markdown_render_seconds"] = time.perf_counter() - started
    measurement["markdown_bytes"] = len(markdown_text.encode("utf-8"))
    del markdown_text

    console = _console()
    started = time.perf_counter()
    render_terminal(result, console)
    measurement["terminal_render_seconds"] = time.perf_counter() - started

    console = _console()
    started = time.perf_counter()
    render_terminal(result, console, verbose=True)
    measurement["terminal_verbose_render_seconds"] = time.perf_counter() - started

    del items, finding, console
    return measurement


def _measure_allocation(size: int) -> dict[str, float]:
    """Bytes allocated and still held by the item graph, and the peak reaching it.

    Its own pass: ``tracemalloc`` hooks every allocation, so a construction
    timed under it measures the profiler as much as the code. ``retained``
    covers the items, the list they are built in, and the parent's tuple --
    everything a rule holds alive while its finding exists.
    """
    gc.collect()
    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        items = make_items(size)
        finding = make_finding(items)
        current, peak = tracemalloc.get_traced_memory()
        del items, finding
    finally:
        tracemalloc.stop()
    return {
        "traced_retained_bytes": float(current - baseline),
        "traced_peak_bytes": float(peak - baseline),
    }


def _peak_rss() -> int:
    observation = worker.peak_rss_observation()
    return int(observation["value"]) if observation["available"] else 0


def rss_probe(size: int) -> dict[str, Any]:
    """Peak resident growth for one pass at ``size``, in a process that did nothing else.

    RSS is a process high-water mark, so reading it in the measuring process
    reports the largest of every earlier size, every rep and tracemalloc's own
    tables for all of them: measured that way the default run reported 1.0 GB,
    against the 0.43 GB one pass at the largest size actually grows by. That is
    the same reason ``bench/worker.py`` exists. Growth is stated against the
    reading taken once the fixture is scanned, in ``resource.getrusage``'s
    platform unit, never normalised.
    """
    observation = worker.peak_rss_observation()
    if not observation["available"]:
        return {
            "available": False,
            "reason": f"peak RSS unavailable on {observation['platform']}",
            "unit": observation["unit"],
            "platform": observation["platform"],
        }
    readings = {"baseline": _peak_rss()}
    with _fixture_result() as result:
        readings["after_fixture"] = _peak_rss()
        items = make_items(size)
        finding = make_finding(items)
        readings["after_items"] = _peak_rss()
        del items, finding
        _measure_once(size, result)
        readings["after_pass"] = _peak_rss()
    return {
        "available": True,
        "unit": observation["unit"],
        "platform": observation["platform"],
        "source": observation["source"],
        "size": size,
        "readings": readings,
        "items_growth": readings["after_items"] - readings["after_fixture"],
        "pass_peak_growth": readings["after_pass"] - readings["after_fixture"],
    }


def worker_pass(size: int) -> dict[str, Any]:
    """Timings and allocation for one pass at ``size``, in a process of its own.

    One measured pass per process, because the alternative was measurably wrong.
    With all three sizes measured in one interpreter, construction at 10,000
    items read 3.27 us/item when the previous rep's 100,000-item pass had just
    released 400 MB, and 2.59 us/item when it had not -- a 26% difference
    decided by measurement order alone. ``bench/worker.py`` exists for the same
    reason.
    """
    with _fixture_result() as result:
        _measure_once(WARMUP_ITEMS, result)
        gc.collect()
        timings = _measure_once(size, result)
    gc.collect()
    allocation = _measure_allocation(size)
    return {"available": True, "size": size, "samples": {**timings, **allocation}}


def _run_worker(mode: str, size: int) -> dict[str, Any]:
    command = [sys.executable, "-W", "ignore", str(_MODULE_PATH), mode, str(size)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
            cwd=_REPOSITORY_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"{mode} timed out after {_WORKER_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"available": False, "reason": f"could not start {mode}: {exc}"}
    if completed.returncode != 0:
        return {
            "available": False,
            "reason": f"{mode} exited {completed.returncode}",
            "stderr_tail": completed.stderr[-2000:],
        }
    try:
        record: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"{mode} emitted invalid JSON: {exc}"}
    return record


def _measure_resident(size: int, *, reps: int) -> dict[str, Any]:
    """Resident growth over ``reps`` independent probe processes, or why not."""
    probes = [_run_worker("--rss-probe", size) for _ in range(reps)]
    unavailable = next((probe for probe in probes if not probe.get("available")), None)
    if unavailable is not None:
        return unavailable
    return {
        "available": True,
        "unit": probes[0]["unit"],
        "platform": probes[0]["platform"],
        "source": f"{probes[0]['source']}, one dedicated subprocess per rep",
        "reps": reps,
        "baseline": _distribution(
            [float(probe["readings"]["after_fixture"]) for probe in probes], size=size
        ),
        "items_growth": _distribution([float(probe["items_growth"]) for probe in probes], size=size),
        "pass_peak_growth": _distribution(
            [float(probe["pass_peak_growth"]) for probe in probes], size=size
        ),
    }


def _unit(name: str) -> str:
    if name.endswith("_seconds"):
        return "seconds"
    if name.endswith("_bytes"):
        return "bytes"
    raise ValueError(f"metric {name!r} does not name its unit")


def _distribution(samples: list[float], *, size: int) -> dict[str, Any]:
    """Median, spread and per-item cost. Never a lone sample presented as a result."""
    median = statistics.median(samples)
    return {
        "median": median,
        "min": min(samples),
        "max": max(samples),
        "spread": (max(samples) - min(samples)) / median if median else 0.0,
        "per_item": median / size,
        "samples": samples,
    }


def _size_record(
    size: int,
    samples: dict[str, list[float]],
    *,
    reps: int,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One size's record, or why it has none.

    A single failed rep makes the size unavailable: a report over fewer reps
    than asked for is a different, unstated measurement, not a smaller version
    of this one.
    """
    if failure is not None:
        record = {"size": size, "reps": reps, "available": False, "reason": failure.get("reason")}
        if "stderr_tail" in failure:
            record["stderr_tail"] = failure["stderr_tail"]
        return record
    return {
        "size": size,
        "reps": reps,
        "available": True,
        "metrics": {
            name: {"unit": _unit(name), **_distribution(values, size=size)}
            for name, values in samples.items()
        },
        "resident": _measure_resident(size, reps=reps),
    }


def _scaling(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-item cost at the largest size against the smallest, metric by metric."""
    ordered = sorted(
        (record for record in results if record.get("available")),
        key=lambda record: int(record["size"]),
    )
    if len(ordered) < 2:
        return {
            "comparable": False,
            "reason": "growth needs at least two measured sizes",
            "threshold": PER_ITEM_GROWTH_THRESHOLD,
            "worse_than_linear": [],
            "metrics": {},
        }
    smallest, largest = ordered[0], ordered[-1]
    metrics: dict[str, Any] = {}
    for name, small in smallest["metrics"].items():
        large = largest["metrics"][name]
        ratio = large["per_item"] / small["per_item"] if small["per_item"] else None
        metrics[name] = {
            "from_size": smallest["size"],
            "to_size": largest["size"],
            "per_item_ratio": ratio,
            "worse_than_linear": ratio is not None and ratio > PER_ITEM_GROWTH_THRESHOLD,
        }
    return {
        "comparable": True,
        "threshold": PER_ITEM_GROWTH_THRESHOLD,
        "worse_than_linear": sorted(
            name for name, record in metrics.items() if record["worse_than_linear"]
        ),
        "metrics": metrics,
    }


def measure(sizes: Sequence[int], *, reps: int = DEFAULT_REPS) -> dict[str, Any]:
    """Every size, one fresh process per rep, interleaved.

    Interleaved rather than one size run to completion: the per-item ratios are
    a comparison *between* sizes, so a size that happened to run while the
    machine was busy would carry drift its neighbours do not. Measured one size
    at a time, two runs of this file put ``to_dict`` per-item growth at 1.49x
    and at 1.17x; interleaved and isolated, two runs agree on every metric's
    ratio to within 0.04x. That is the same lesson ``bench/runner.py``'s ``ab``
    mode records for its two source trees.
    """
    if not sizes:
        raise ValueError("no sizes to measure")
    for size in sizes:
        if size < 1:
            raise ValueError(f"size must be at least 1, got {size}")
    if reps < 1:
        raise ValueError(f"reps must be at least 1, got {reps}")

    ordered = list(sizes)
    samples: list[dict[str, list[float]]] = [{} for _ in ordered]
    failures: list[dict[str, Any] | None] = [None for _ in ordered]
    for _ in range(reps):
        for index, size in enumerate(ordered):
            if failures[index] is not None:
                continue
            record = _run_worker("--measure-once", size)
            if not record.get("available"):
                failures[index] = record
                continue
            for name, value in record["samples"].items():
                samples[index].setdefault(name, []).append(float(value))
    results = [
        _size_record(size, samples[index], reps=reps, failure=failures[index])
        for index, size in enumerate(ordered)
    ]

    return {
        "schema": REPORT_SCHEMA,
        "provenance": runner._provenance(),
        "reps": reps,
        "sizes": list(sizes),
        "item_shape": item_shape(),
        "notes": {
            "allocation_pass": (
                "tracemalloc runs in its own pass; it hooks every allocation and would "
                "distort the construction timings it shares a rep with"
            ),
            "resident": (
                "resident growth is a peak-RSS difference taken in a probe process that "
                "measures nothing else, in resource.getrusage's platform unit; it is a "
                "different quantity from the traced_* byte metrics, which count "
                "allocations rather than pages, and the two are not comparable"
            ),
            "isolation": (
                "every rep of every size is measured in its own process, so no size "
                "inherits the heap another one left behind"
            ),
            "warmup": (
                f"each process discards one pass over {WARMUP_ITEMS} items before the "
                "measured pass, so no size carries the cost of every code path's first "
                "execution"
            ),
            "interleaving": (
                "each rep measures every size in turn, so machine drift lands on all of "
                "them rather than on whichever size ran while the machine was busy"
            ),
            "growth_flag": (
                "worse_than_linear screens the per-item cost at the largest size against "
                "the smallest; it is a flag to investigate, not a verdict. A larger live "
                "object graph makes CPython's generational collector traverse more on each "
                "pass, which moves a per-item timing without any algorithm changing"
            ),
            "check_result": (
                "the reporters render one CheckResult from a three-file fixture "
                "repository, so the only difference between two sizes is the children"
            ),
        },
        "results": results,
        "scaling": _scaling(results),
    }


def parse_sizes(text: str) -> tuple[int, ...]:
    """Comma-separated positive item counts, or an explicit refusal.

    A garbled or non-positive size is refused here, where the offending token
    can be named, rather than dividing by zero in a per-item cost later.
    """
    tokens = [token.strip() for token in text.split(",") if token.strip()]
    if not tokens:
        raise argparse.ArgumentTypeError(f"no sizes in {text!r}")
    sizes: list[int] = []
    for token in tokens:
        try:
            size = int(token)
        except ValueError:
            raise argparse.ArgumentTypeError(f"size {token!r} is not an integer") from None
        if size < 1:
            raise argparse.ArgumentTypeError(f"size {token!r} is not positive")
        sizes.append(size)
    return tuple(sizes)


def _format_value(value: float, unit: str) -> str:
    if unit == "seconds":
        return f"{value:.3f} s" if value >= 1.0 else f"{value * 1000:.3f} ms"
    if value >= 1 << 20:
        return f"{value / (1 << 20):.2f} MiB"
    return f"{value:,.0f} B"


def _format_per_item(value: float, unit: str) -> str:
    if unit == "seconds":
        return f"{value * 1e6:.3f} us/item"
    return f"{value:,.1f} B/item"


def _render(report: dict[str, Any]) -> str:
    provenance = report["provenance"]
    ordered = sorted(report["results"], key=lambda record: int(record["size"]))
    measured = [record for record in ordered if record.get("available")]
    scaling = report["scaling"]
    shape = report["item_shape"]
    labels = [format(int(record["size"]), ",") + " items" for record in measured]
    lines = [
        "FindingItem cost at " + ("/".join(labels) or "no measured size")
        + f", {report['reps']} reps",
        f"python {provenance['python']} on {provenance['platform']} "
        f"({provenance['machine']}), adduce {provenance['adduce_version']}",
        f"item: {shape['example_json_bytes']} JSON bytes, "
        f"location on 1 in {shape['locations_on_every_nth_item']} items",
        "",
    ]

    if measured:
        lines.append(
            f"{'metric':34s}" + "".join(f"{label:30s}" for label in labels) + "per-item x"
        )
        for name in measured[0]["metrics"]:
            unit = measured[0]["metrics"][name]["unit"]
            row = f"{name:34s}"
            for record in measured:
                metric = record["metrics"][name]
                cell = (
                    f"{_format_value(metric['median'], unit)} "
                    f"({_format_per_item(metric['per_item'], unit)})"
                )
                row += f"{cell:30s}"
            ratio = scaling["metrics"].get(name, {}).get("per_item_ratio")
            lines.append(row + ("n/a" if ratio is None else f"{ratio:.2f}"))
        lines.append("")

    for record in ordered:
        if not record.get("available"):
            lines.append(f"{record['size']:>8,} items: unmeasured: {record.get('reason')}")
            continue
        spreads = [metric["spread"] for metric in record["metrics"].values()]
        lines.append(f"{record['size']:>8,} items: worst rep spread {max(spreads):.1%}")
        resident = record["resident"]
        if not resident["available"]:
            lines.append(f"{'':>8}  resident unmeasured: {resident.get('reason')}")
            continue
        unit = resident["unit"]
        items_growth = resident["items_growth"]
        pass_peak = resident["pass_peak_growth"]
        lines.append(
            f"{'':>8}  resident +{items_growth['median']:,.0f} {unit} holding the items "
            f"({items_growth['per_item']:,.0f} {unit}/item); "
            f"+{pass_peak['median']:,.0f} {unit} peak over one full pass "
            f"({pass_peak['per_item']:,.0f} {unit}/item)"
        )

    flagged = scaling["worse_than_linear"]
    lines.append("")
    lines.append(
        "worse than linear (per-item cost grew more than "
        f"{scaling['threshold']:.2f}x): {', '.join(flagged) if flagged else 'nothing'}"
        if scaling["comparable"]
        else f"growth not assessed: {scaling['reason']}"
    )
    for note in report["notes"].values():
        lines.append(f"note: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=parse_sizes,
        default=parse_sizes(DEFAULT_SIZES),
        help="comma-separated item counts to measure (default: %(default)s)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=DEFAULT_REPS,
        help="repetitions per size; the report carries the median and the spread",
    )
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--measure-once",
        type=int,
        metavar="SIZE",
        help="internal: print one process's timings and allocation at SIZE as JSON and exit",
    )
    parser.add_argument(
        "--rss-probe",
        type=int,
        metavar="SIZE",
        help="internal: print one process's resident readings at SIZE as JSON and exit",
    )
    arguments = parser.parse_args(argv)

    if arguments.reps < 1:
        parser.error(f"--reps must be at least 1, got {arguments.reps}")

    for flag, size, measurement in (
        ("--measure-once", arguments.measure_once, worker_pass),
        ("--rss-probe", arguments.rss_probe, rss_probe),
    ):
        if size is None:
            continue
        if size < 1:
            parser.error(f"{flag} must be at least 1, got {size}")
        json.dump(measurement(size), sys.stdout)
        return 0

    report = measure(arguments.sizes, reps=arguments.reps)
    print(_render(report))
    if arguments.output is not None:
        rendered = json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        replace_text_regular(
            arguments.output,
            rendered,
            label="finding item benchmark report",
            parent_label="finding item benchmark report directory",
        )
        print(f"written to {arguments.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
