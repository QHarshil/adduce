#!/usr/bin/env python3
"""Summarise one validated corpus run without exceeding its evidence.

The purposive pilot supports descriptive medians, spread, operational
outcomes, and finding prevalence only. It does not support significance,
population false-positive, calibrated-tier, or stress-cohort quality claims.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

if __package__:
    from .run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        validate_run_evidence_with_digest,
    )
else:
    from run_contract import (
        RunContractError,
        ensure_output_outside,
        load_json_object_bytes,
        require_current_harness_file,
        validate_run_evidence_with_digest,
    )


def _scores(rows: list[dict[str, str]]) -> list[float]:
    return sorted(float(r["score"]) for r in rows if r.get("score") not in ("", None))


def _spread(scores: list[float]) -> str:
    if not scores:
        return "n=0 — no completed runs"
    if len(scores) < 4:
        return (
            f"n={len(scores)} median={statistics.median(scores):.1f} "
            f"range=[{scores[0]:.1f}, {scores[-1]:.1f}] (too few for an IQR)"
        )
    q1, _, q3 = statistics.quantiles(scores, n=4, method="inclusive")
    return (
        f"n={len(scores)} median={statistics.median(scores):.1f} "
        f"IQR=[{q1:.1f}, {q3:.1f}] range=[{scores[0]:.1f}, {scores[-1]:.1f}]"
    )


def _acquisition_line(rows: list[dict[str, str]]) -> str:
    failed = sum(1 for row in rows if row.get("run_status") == "acquisition_failed")
    partial = sum(1 for row in rows if row.get("acquisition_status") == "partial")
    total = len(rows)
    rate = failed / total if total else 0.0
    return f"{failed}/{total} failed ({rate:.0%}); {partial}/{total} were partial"


def _scanner_line(rows: list[dict[str, str]]) -> str:
    attempted = [row for row in rows if row.get("run_status") != "acquisition_failed"]
    scanner_failures = sum(
        1 for row in attempted if row.get("run_status") in {"scanner_crash", "scanner_timeout"}
    )
    contract_failures = sum(1 for row in attempted if row.get("run_status") == "contract_failed")
    timed_out = sum(1 for row in attempted if row.get("run_status") == "scanner_timeout")
    return (
        f"{scanner_failures}/{len(attempted)} scanner attempts crashed or timed out "
        f"({timed_out} timeout(s)); {contract_failures} contract failure(s)"
    )


def _runtime_line(rows: list[dict[str, str]]) -> str:
    runtimes = sorted(float(r["runtime_seconds"]) for r in rows if r.get("runtime_seconds"))
    if not runtimes:
        return "no runtimes recorded"
    p95_index = max(0, min(len(runtimes) - 1, round(0.95 * len(runtimes) + 0.5) - 1))
    return (
        f"median {statistics.median(runtimes):.1f}s, p95 {runtimes[p95_index]:.1f}s, "
        f"range [{runtimes[0]:.1f}s, {runtimes[-1]:.1f}s] over {len(runtimes)} runs"
    )


def _chattiest_rules(
    artifacts: dict[str, bytes], rows: list[dict[str, str]]
) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in rows:
        raw = artifacts.get(f"raw_json/{row['id']}.json")
        if raw is None:
            continue
        payload = load_json_object_bytes(raw, f"raw_json/{row['id']}.json")
        for finding in payload.get("findings", []):
            if finding.get("status") in ("fail", "partial") and not finding.get("suppressed"):
                counter[finding["rule_id"]] += 1
    return counter.most_common(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", type=Path, required=True, help="a corpus/outputs/<version>/ directory"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="summary destination (defaults beside, never inside, the immutable run)",
    )
    args = parser.parse_args()

    summary = args.out or args.run.parent / f"{args.run.name}-summary.md"
    try:
        ensure_output_outside(summary, [args.run])
        meta, artifacts, rows, meta_digest = validate_run_evidence_with_digest(args.run)
        require_current_harness_file(meta, "scripts/summarize.py", Path(__file__))
    except RunContractError as exc:
        sys.exit(f"invalid corpus run: {exc}")

    badged = [r for r in rows if r["cohort"].startswith("badged_")]
    unvetted = [r for r in rows if r["cohort"] == "unvetted"]
    stress = [r for r in rows if r["cohort"] == "stress"]
    badged_scores = _scores(badged)
    unvetted_scores = _scores(unvetted)

    lines = [
        f"# Validation summary (adduce {meta.get('adduce_version', 'unknown')})",
        "",
        f"- run ID: `{meta['run_id']}`",
        f"- run metadata SHA-256: `{meta_digest}`",
        "",
        "Distributional signal only: medians and spread over small cohorts.",
        "No significance statistics are computed, and no false-positive rate is",
        "claimed here. Manual review is required, and this purposive sample does not",
        "estimate a population rate.",
        "",
        "## Labelled cohorts",
        "",
        f"- badged (all badged_* sub-cohorts): {_spread(badged_scores)}",
        f"- unvetted: {_spread(unvetted_scores)}",
    ]
    for name in ("badged_functional", "badged_available", "badged_venue"):
        sub_scores = _scores([r for r in badged if r["cohort"] == name])
        if sub_scores:
            lines.append(f"  - {name}: {_spread(sub_scores)}")
    if badged_scores and unvetted_scores:
        low, high = (
            max(badged_scores[0], unvetted_scores[0]),
            min(badged_scores[-1], unvetted_scores[-1]),
        )
        overlap = "none" if low > high else f"[{low:.1f}, {high:.1f}]"
        above = sum(1 for s in unvetted_scores if s >= statistics.median(badged_scores))
        lines += [
            "",
            f"- score-range overlap between the cohorts: {overlap}",
            f"- unvetted repos scoring at or above the badged median: {above}/{len(unvetted_scores)}",
        ]

    cat_columns = sorted({k for r in rows for k in r if k.startswith("cat_") and r.get(k)})
    if cat_columns and badged_scores and unvetted_scores:
        lines += ["", "## Per-category median gaps (badged − unvetted, percentage points)", ""]
        for column in cat_columns:
            b = [float(r[column]) for r in badged if r.get(column)]
            u = [float(r[column]) for r in unvetted if r.get(column)]
            if b and u:
                gap = statistics.median(b) - statistics.median(u)
                lines.append(f"- {column.removeprefix('cat_')}: {gap:+.1f}")

    lines += [
        "",
        "## Robustness",
        "",
        f"- acquisition (all cohorts): {_acquisition_line(rows)}",
        f"- scanner execution (all cohorts): {_scanner_line(rows)}",
        f"- runtime: {_runtime_line(rows)}",
    ]
    if stress:
        lines += [
            f"- stress cohort acquisition: {_acquisition_line(stress)}",
            f"- stress cohort scanner execution: {_scanner_line(stress)}",
            "- stress scores back no claim; the cohort probes operational limits and chatty output.",
        ]
        chatty = _chattiest_rules(artifacts, stress)
        if chatty:
            lines += [
                "",
                "### Chattiest rules on the stress cohort (fail/partial prevalence only)",
                "",
                "Prevalence is not a manual noise label.",
                "",
            ]
            lines += [f"- {rule_id}: {count}" for rule_id, count in chatty]

    if summary.exists():
        sys.exit(f"refusing to overwrite existing summary: {summary}")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
