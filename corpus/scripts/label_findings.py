#!/usr/bin/env python3
"""Hand-label sampled findings; report the rates only labels can produce.

The false-positive rate is a manual measurement: this loop shows each
unlabelled finding and writes the file back after every answer, so a
labelling session can be interrupted and resumed without losing work.
--report prints label counts and the actionable / false-positive / unclear
rates together with the sample size, because a rate without its n is not a
publishable number.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

LABELS = {
    "1": "true_positive_actionable",
    "2": "true_positive_minor",
    "3": "false_positive",
    "4": "unclear_unverifiable",
    "5": "low_value_noise",
}
LABEL_NAMES = frozenset(LABELS.values())


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def report(entries: list[dict]) -> int:
    labelled = [e for e in entries if e.get("label")]
    counts = Counter(e["label"] for e in labelled)
    print(f"labelled {len(labelled)} of {len(entries)} sampled findings")
    for name in LABELS.values():
        print(f"  {name}: {counts.get(name, 0)}")
    if not labelled:
        print("no rates: nothing labelled yet.")
        return 0
    n = len(labelled)
    print(f"actionable rate: {counts.get('true_positive_actionable', 0) / n:.0%} (n={n})")
    print(f"false-positive rate: {counts.get('false_positive', 0) / n:.0%} (n={n})")
    print(f"unclear rate: {counts.get('unclear_unverifiable', 0) / n:.0%} (n={n})")
    if len(labelled) < len(entries):
        print(f"note: {len(entries) - len(labelled)} findings still unlabelled; rates are provisional.")
    return 0


def label_loop(path: Path, entries: list[dict]) -> int:
    pending = [e for e in entries if not e.get("label")]
    if not pending:
        print("all findings already labelled; use --report for the rates.")
        return 0
    print(f"{len(pending)} unlabelled finding(s). Answers: 1-5, label name, or q to stop.\n")
    for entry in entries:
        if entry.get("label"):
            continue
        print(f"--- {entry['repo']} [{entry['cohort']}] {entry['rule_id']} ({entry['status']})")
        print(f"    {entry.get('message', '')}")
        for location in entry.get("locations", [])[:5]:
            print(f"    at {location}")
        for key, name in LABELS.items():
            print(f"    [{key}] {name}")
        while True:
            try:
                answer = input("label> ").strip()
            except EOFError:
                answer = "q"
            if answer == "q":
                print("stopped; progress saved.")
                return 0
            if answer in LABELS:
                entry["label"] = LABELS[answer]
                break
            if answer in LABEL_NAMES:
                entry["label"] = answer
                break
            print("  unrecognised; enter 1-5, a label name, or q.")
        save(path, entries)  # persist after every answer so the session is resumable
        print()
    print("all findings labelled.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jsonl", type=Path, help="a findings_sample_*.jsonl file")
    parser.add_argument("--report", action="store_true", help="print label counts and rates instead of labelling")
    args = parser.parse_args()

    if not args.jsonl.is_file():
        sys.exit(f"missing {args.jsonl}; run sample_findings.py first.")
    entries = load(args.jsonl)
    if not entries:
        sys.exit(f"{args.jsonl} is empty.")
    return report(entries) if args.report else label_loop(args.jsonl, entries)


if __name__ == "__main__":
    raise SystemExit(main())
