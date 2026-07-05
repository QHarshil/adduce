#!/usr/bin/env python3
"""Summarise a validation run into summary.md, within honesty limits.

Only distributional statements are computed — medians, IQRs, ranges,
overlap — because with ~50 labelled repositories that is all the data
supports. No significance statistics are invented, and nothing here claims a
false-positive rate: that number can only come from hand-labelled findings
(label_findings.py --report). The stress cohort is summarised for crash rate
and noise only; its scores back no claim.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def _read_rows(run_dir: Path) -> list[dict[str, str]]:
    combined = run_dir / "combined.csv"
    if not combined.is_file():
        sys.exit(f"missing {combined}; run run_validation.py first.")
    with combined.open(newline="") as handle:
        return list(csv.DictReader(handle))


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


def _crash_line(rows: list[dict[str, str]]) -> str:
    crashed = sum(1 for r in rows if r.get("crash") == "True")
    timed_out = sum(1 for r in rows if r.get("timeout") == "True")
    total = len(rows)
    rate = crashed / total if total else 0.0
    return f"{crashed}/{total} rows crashed or timed out ({rate:.0%}; {timed_out} of those were timeouts)"


def _runtime_line(rows: list[dict[str, str]]) -> str:
    runtimes = sorted(float(r["runtime_seconds"]) for r in rows if r.get("runtime_seconds"))
    if not runtimes:
        return "no runtimes recorded"
    return (
        f"median {statistics.median(runtimes):.1f}s, "
        f"range [{runtimes[0]:.1f}s, {runtimes[-1]:.1f}s] over {len(runtimes)} runs"
    )


def _top_noisy_rules(run_dir: Path, rows: list[dict[str, str]]) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in rows:
        raw = run_dir / "raw_json" / f"{row['id']}.json"
        if not raw.is_file():
            continue
        payload = json.loads(raw.read_text(encoding="utf-8"))
        for finding in payload.get("findings", []):
            if finding.get("status") in ("fail", "partial") and not finding.get("suppressed"):
                counter[finding["rule_id"]] += 1
    return counter.most_common(10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a corpus/outputs/<version>/ directory")
    args = parser.parse_args()

    rows = _read_rows(args.run)
    meta = {}
    meta_path = args.run / "run_meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    badged = [r for r in rows if r["cohort"].startswith("badged_")]
    unvetted = [r for r in rows if r["cohort"] == "unvetted"]
    stress = [r for r in rows if r["cohort"] == "stress"]
    badged_scores = _scores(badged)
    unvetted_scores = _scores(unvetted)

    lines = [
        f"# Validation summary (adduce {meta.get('adduce_version', 'unknown')})",
        "",
        "Distributional signal only: medians and spread over small cohorts.",
        "No significance statistics are computed, and no false-positive rate is",
        "claimed here — that comes only from hand labels (label_findings.py --report).",
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
        low, high = max(badged_scores[0], unvetted_scores[0]), min(badged_scores[-1], unvetted_scores[-1])
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
        f"- crash rate (all cohorts): {_crash_line(rows)}",
        f"- runtime: {_runtime_line(rows)}",
    ]
    if stress:
        lines += [
            f"- stress cohort crash rate: {_crash_line(stress)}",
            "- stress scores back no claim; the cohort exists to find crashes and noise.",
        ]
        noisy = _top_noisy_rules(args.run, stress)
        if noisy:
            lines += ["", "### Top noisy rules on the stress cohort (fail/partial counts)", ""]
            lines += [f"- {rule_id}: {count}" for rule_id, count in noisy]

    summary = args.run / "summary.md"
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
