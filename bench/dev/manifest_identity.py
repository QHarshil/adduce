#!/usr/bin/env python3
"""Byte identity of the drafted manifest, per synthetic case, across two source trees.

The instrument the synthetic corpus already had is a byte-identity check over
the default JSON report: run ``check`` with ``--paper`` at each case and
compare the rendered report between two trees. That report carries a claim's
metric, value, location and trail and carries **neither its confidence nor its
resolution method**, so it is structurally blind to a change that moves only
how confidently a number was read -- ``every case identical`` is then a true
negative that could never have been otherwise, not evidence of neutrality. The
baseline demotion shipped under exactly that reading: it moved method and
confidence on 157 dev-set extractions and every synthetic case's report was
identical.

``.adduce/manifest.yaml`` carries both fields, and the row and column labels
naming the cell a claim was read from. So this compares manifests: for every
case directory under ``corpus/synthetic``, the bytes ``adduce manifest <case>
--paper <case>`` would write are digested under each of two source trees, and
the per-case verdict is ``identical`` or ``moved``. What it covers is therefore
every change visible in a drafted manifest -- the claim set, each claim's
metric, value, locator, text and cell labels, and additionally the confidence
and resolution method the JSON report drops -- plus the manifest's paper,
environment, dataset, remote and smoke sections. What it does not do is say a
confidence is *right*: it reports movement, never correctness.

Its reach over claims is whatever the corpus drafts, which is a property of the
corpus and not of this harness: a case drafting no claim can move here only
through those other sections. Read the live figures off the report, which counts
cases and claims on every run, rather than off a number copied into prose that
goes stale the next time a case is added. Measured over the 29 case directories
the corpus held when this was written, 18 draft at least one claim and 87 in
total, the largest being ``synthetic_quoted_baseline_rows`` at 10.

Liveness for that class is measured, not argued. Against the source tree and a
copy of it with the prior-work demotion removed -- a change that can move
nothing but confidence and method -- this reports **two cases moved**:
``synthetic_markup_residue`` on 2 of its 8 claims and
``synthetic_quoted_baseline_rows`` on 6 of its 10, both on ``confidence`` and
``resolution_method`` alone, with every other case identical. The JSON-report
check over the same two trees reports **no case moved at all**. That check is
live for other classes -- suppressing second-header composition moves four cases
in it -- and cannot see this one. The honest limit is that cover for the
demotion class is thin rather than absent: those two cases are the only ones
carrying a citation in a row label, only ``synthetic_quoted_baseline_rows`` also
carries a prior-work section row, and a refinement reaching markup no case
contains would still measure nothing.

``--paper`` is pointed at the case root rather than at ``<case>/paper`` because
not every case carries a paper directory, and the LaTeX collector over the case
root finds the paper wherever there is one. This matches the invocation the case
READMEs document.

**No repository state reaches a digest, and nothing is written inside the
repository.** Each case is copied into a temporary directory outside any work
tree and the manifest is written there, so a case's own ``.adduce`` directory is
never created under ``corpus/synthetic`` -- which is tracked -- and the digest
cannot pick up the commit the way the JSON report's ``/repository/commit``
does. That the manifest carries no git metadata at all was measured rather than
assumed: one case digests identically outside any repository, inside a
repository, and inside that repository after its HEAD moved. The copy is what
is measured, so a case is also measured with no ``.gitignore`` above it, in both
arms alike.

Each arm is a source tree, resolved in a fresh subprocess that inserts it ahead
of everything else on ``sys.path`` (the ``_manifest`` subcommand below), which
is how ``bench/dev/recall.py`` swaps trees. The editable install is a plain path
entry, so this takes precedence -- but a swap that silently failed would make
both arms measure one tree and read as a clean pass, so each arm reports the
directory it actually imported ``adduce`` from and the report states outright
when the two agree. Nothing in this module imports ``adduce`` at module scope;
doing so would pin the parent process to whichever tree loaded first.

Build the arm under test as a pristine baseline plus only the change being
measured (``git archive HEAD src`` gives the baseline), so that an unrelated
edit elsewhere in the working tree cannot leak into the comparison.

A moved case is a measurement, not a verdict: this exits non-zero only when a
case could not be measured at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BENCH_DEV_ROOT = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _BENCH_DEV_ROOT.parents[1]

REPORT_SCHEMA = "adduce-bench-dev-manifest-identity/1"
DEFAULT_CASES_ROOT = _REPOSITORY_ROOT / "corpus" / "synthetic"
DEFAULT_SRC = _REPOSITORY_ROOT / "src"
_MANIFEST_TIMEOUT_SECONDS = 600


# -- discovery ----------------------------------------------------------------


def discover_cases(root: Path) -> list[Path]:
    """Every case directory under ``root``, in a fixed order.

    A directory, because the corpus root also holds ``expectations.yaml``, and
    the case set is whatever is on disk: ``corpus/`` is owned elsewhere and
    ``tests/test_synthetic_corpus.py`` already pins the directory set against
    that file, so hard-coding a count here would only duplicate that assertion
    badly. Dot-prefixed and ``__pycache__`` entries are not cases.
    """
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
    )


# -- one case under one tree --------------------------------------------------


@dataclass(frozen=True)
class CaseManifest:
    """One case's drafted manifest under one source tree.

    ``digest`` is over the written manifest's bytes -- the authoritative
    artifact; the JSON mirror is rendered from the same payload. ``claims`` is
    the manifest's own claim list, kept so a moved digest can be attributed to
    fields rather than only reported as movement. ``loaded_from`` is the
    directory the worker imported ``adduce`` from, which is the only evidence
    that the arm measured the tree it was given. ``mode`` is which of the CLI's
    two branches the case took, and is compared: a case that stops taking the
    author-manifest branch has moved whatever its digest says.
    """

    available: bool
    reason: str | None = None
    digest: str | None = None
    mode: str | None = None
    loaded_from: str | None = None
    claims: tuple[dict[str, Any], ...] = ()


def _case_manifest_from_json(payload: dict[str, Any]) -> CaseManifest:
    def text(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    if not payload.get("available"):
        reason = text("reason") or "manifest unavailable"
        # The worker's own last line, so a refusal names its cause here rather
        # than only in a rerun of the subcommand by hand.
        tail = [line for line in (text("stderr_tail") or "").splitlines() if line.strip()]
        return CaseManifest(available=False, reason=f"{reason}: {tail[-1]}" if tail else reason)
    raw_claims = payload.get("claims")
    claims = (
        tuple(entry for entry in raw_claims if isinstance(entry, dict))
        if isinstance(raw_claims, list)
        else ()
    )
    return CaseManifest(
        available=True,
        digest=text("digest"),
        mode=text("mode"),
        loaded_from=text("adduce_loaded_from"),
        claims=claims,
    )


def _cmd_manifest(arguments: argparse.Namespace) -> int:
    """Draft one case's manifest under ``--src`` and print its digest.

    Always executed in its own subprocess (see :func:`_run_manifest_worker`):
    this is the only place a tree is put on ``sys.path``, and it has to happen
    before the first ``adduce`` import in *this* process.

    The case is copied out of the repository first and the manifest is written
    into the copy, so the shipped write path is exercised without a tracked
    tree ever being touched.

    Both of ``cli.manifest``'s branches are taken here, on the same condition it
    uses. A case carrying an author-written manifest -- ``synthetic_hydra_authority``
    does -- is drafted as ``--refresh`` does it, beside the author's file and
    never over it; every other case is the plain scaffold. Deleting the author's
    file to make every case uniform was the alternative and would have measured a
    case the corpus does not contain.
    """
    case: Path = arguments.case
    src: Path | None = arguments.src

    if not case.is_dir():
        json.dump({"available": False, "reason": f"case path is not a directory: {case}"}, sys.stdout)
        return 0

    if src is not None:
        sys.path.insert(0, str(src.resolve()))
    for name in [module for module in sys.modules if module == "adduce" or module.startswith("adduce.")]:
        del sys.modules[name]

    from adduce.engine import run_check
    from adduce.manifest import write_manifest, write_manifest_proposal
    from adduce.manifest_builder import scaffold_manifest

    with tempfile.TemporaryDirectory(prefix="adduce-manifest-identity-") as scratch:
        # Resolved: the write boundary refuses a symbolic-link ancestor, and
        # macOS hands tempfile a path under /var, which is a link to /private/var.
        copy = Path(scratch).resolve() / case.name
        shutil.copytree(case, copy)
        result = run_check(copy, paper=copy)
        authored = result.evidence.manifest.exists
        draft = scaffold_manifest(result.evidence, refresh=authored)
        target = (
            write_manifest_proposal(copy, draft) if authored else write_manifest(copy, draft)
        )
        written = target.read_bytes()
        json.dump(
            {
                "available": True,
                "adduce_loaded_from": str(Path(sys.modules["adduce"].__file__ or "").parent),
                "mode": "refresh_proposal" if authored else "scaffold",
                "digest": hashlib.sha256(written).hexdigest(),
                "claims": draft.to_dict().get("claims", []),
            },
            sys.stdout,
        )
    return 0


def _run_manifest_worker(*, case: Path, src: Path | None) -> dict[str, Any]:
    """Shell out to this file's ``_manifest`` subcommand and parse its JSON."""
    command = [
        sys.executable,
        "-B",
        "-W",
        "ignore",
        str(_BENCH_DEV_ROOT / "manifest_identity.py"),
        "_manifest",
        "--case",
        str(case),
    ]
    if src is not None:
        command.extend(["--src", str(src)])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_MANIFEST_TIMEOUT_SECONDS,
            cwd=_REPOSITORY_ROOT,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"manifest timed out after {_MANIFEST_TIMEOUT_SECONDS}s"}
    except OSError as exc:
        return {"available": False, "reason": f"could not start manifest worker: {exc}"}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": f"manifest worker exited {result.returncode}",
            "stderr_tail": result.stderr[-2000:],
        }
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"manifest worker emitted invalid JSON: {exc}"}
    return payload


def src_refusal(src: Path | None) -> str | None:
    """Why this tree cannot be an arm, or ``None``.

    A path with no ``adduce`` package under it is refused rather than measured.
    ``sys.path.insert`` of a directory that holds nothing does not fail: the
    import falls through to the editable install, so the arm would quietly
    measure this repository's own tree and a comparison against one bogus arm
    would look like a difference between two trees.
    """
    if src is None:
        return None
    if not (src / "adduce" / "__init__.py").is_file():
        return f"no adduce package under {src}"
    return None


def measure_case(case: Path, *, src: Path | None = None) -> CaseManifest:
    """One case's manifest under one tree, refusing bad inputs without a worker."""
    if not case.is_dir():
        return CaseManifest(available=False, reason=f"case directory not found: {case}")
    if refusal := src_refusal(src):
        return CaseManifest(available=False, reason=refusal)
    return _case_manifest_from_json(_run_manifest_worker(case=case, src=src))


# -- one case across two trees ------------------------------------------------


@dataclass(frozen=True)
class CaseComparison:
    """One case measured under both arms.

    ``moved_fields`` names the claim fields that differ, which is what
    separates a confidence-only move from a changed extraction. It is empty
    when the two arms draft different numbers of claims -- there is then no
    correspondence to read fields across, and :meth:`summary` reports the two
    counts instead. The digest, not the field diff, decides ``identical``: a
    manifest also carries paper, environment, dataset, remote and smoke
    sections, so a case can move with no claim field moving at all.
    """

    case_id: str
    before: CaseManifest
    after: CaseManifest

    @property
    def available(self) -> bool:
        return self.before.available and self.after.available

    @property
    def identical(self) -> bool:
        return (
            self.available
            and self.before.digest is not None
            and self.before.digest == self.after.digest
            and self.before.mode == self.after.mode
        )

    @property
    def claim_counts_agree(self) -> bool:
        return len(self.before.claims) == len(self.after.claims)

    @property
    def moved_claims(self) -> int:
        if not self.available or not self.claim_counts_agree:
            return 0
        return sum(
            1
            for before, after in zip(self.before.claims, self.after.claims, strict=True)
            if before != after
        )

    @property
    def moved_fields(self) -> tuple[str, ...]:
        """Every claim key the two arms disagree on, over positionally paired claims.

        Positional pairing is sound only while the claim count holds: the
        manifest numbers its claims ``C1``..``Cn`` in extraction order, so a
        change that adds or drops one renumbers everything after it and a
        field-level diff would report noise. That case reports the counts
        instead.
        """
        if not self.available or not self.claim_counts_agree:
            return ()
        fields: set[str] = set()
        for before, after in zip(self.before.claims, self.after.claims, strict=True):
            for key in set(before) | set(after):
                if before.get(key) != after.get(key):
                    fields.add(key)
        return tuple(sorted(fields))

    def summary(self) -> str:
        if not self.available:
            reason = self.before.reason or self.after.reason or "unavailable"
            return f"unavailable: {reason}"
        if self.identical:
            return "identical"
        if self.before.mode != self.after.mode:
            return f"moved (mode {self.before.mode} -> {self.after.mode})"
        if not self.claim_counts_agree:
            return f"moved (claims {len(self.before.claims)} -> {len(self.after.claims)})"
        if not self.moved_fields:
            return "moved (no claim field: the difference is elsewhere in the manifest)"
        fields = ", ".join(self.moved_fields)
        return f"moved ({fields} on {self.moved_claims} of {len(self.before.claims)} claim(s))"


def compare_case(case: Path, *, before: Path | None, after: Path | None) -> CaseComparison:
    return CaseComparison(
        case_id=case.name,
        before=measure_case(case, src=before),
        after=measure_case(case, src=after),
    )


def compare_cases(
    cases: list[Path], *, before: Path | None, after: Path | None
) -> list[CaseComparison]:
    return [compare_case(case, before=before, after=after) for case in cases]


# -- reporting ----------------------------------------------------------------


def _loaded_from(comparisons: list[CaseComparison], *, arm: str) -> str | None:
    for comparison in comparisons:
        recorded = (comparison.before if arm == "before" else comparison.after).loaded_from
        if recorded is not None:
            return recorded
    return None


def build_report(
    comparisons: list[CaseComparison], *, before: Path | None, after: Path | None
) -> dict[str, Any]:
    before_loaded = _loaded_from(comparisons, arm="before")
    after_loaded = _loaded_from(comparisons, arm="after")
    return {
        "schema": REPORT_SCHEMA,
        "arms": {
            "before": str(before) if before is not None else str(DEFAULT_SRC),
            "after": str(after) if after is not None else str(DEFAULT_SRC),
            "before_loaded_from": before_loaded,
            "after_loaded_from": after_loaded,
            # Stated, not inferred by the reader: two arms that resolved one
            # tree measure nothing, however clean the result looks.
            "arms_loaded_the_same_tree": (
                before_loaded is not None and before_loaded == after_loaded
            ),
        },
        "results": [
            {
                "case_id": comparison.case_id,
                "identical": comparison.identical,
                "available": comparison.available,
                "before_digest": comparison.before.digest,
                "after_digest": comparison.after.digest,
                "before_mode": comparison.before.mode,
                "after_mode": comparison.after.mode,
                "moved_claims": comparison.moved_claims,
                "moved_fields": list(comparison.moved_fields),
                "summary": comparison.summary(),
            }
            for comparison in comparisons
        ],
        "summary": {
            "cases": len(comparisons),
            "identical": sum(1 for c in comparisons if c.identical),
            "moved": sum(1 for c in comparisons if c.available and not c.identical),
            "unavailable": sum(1 for c in comparisons if not c.available),
        },
    }


def render_text(report: dict[str, Any]) -> str:
    arms = report["arms"]
    lines = [
        f"before: {arms['before']} (loaded {arms['before_loaded_from']})",
        f"after:  {arms['after']} (loaded {arms['after_loaded_from']})",
    ]
    if arms["arms_loaded_the_same_tree"]:
        lines.append("both arms loaded adduce from the same directory: this comparison is vacuous")
    lines.append("")
    lines.append(f"{'case':38s} {'before':10s} {'after':10s} verdict")
    for record in report["results"]:
        before = (record["before_digest"] or "-")[:8]
        after = (record["after_digest"] or "-")[:8]
        lines.append(f"{record['case_id']:38s} {before:10s} {after:10s} {record['summary']}")
    summary = report["summary"]
    lines.append(
        f"\n{summary['cases']} case(s): {summary['identical']} identical, "
        f"{summary['moved']} moved, {summary['unavailable']} unavailable"
    )
    return "\n".join(lines)


# -- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker = subparsers.add_parser(
        "_manifest", help="internal worker: draft one case's manifest and digest it"
    )
    worker.add_argument("--case", type=Path, required=True)
    worker.add_argument("--src", type=Path, help="resolve adduce from this tree instead of <repo>/src")

    compare = subparsers.add_parser(
        "compare", help="digest every synthetic case's manifest under two source trees"
    )
    compare.add_argument("--cases-root", type=Path, default=DEFAULT_CASES_ROOT)
    compare.add_argument("--before", type=Path, help="the reference tree (default <repo>/src)")
    compare.add_argument("--after", type=Path, help="the tree under test (default <repo>/src)")
    compare.add_argument(
        "--only", action="append", help="restrict to this case id; repeatable"
    )
    compare.add_argument("--json", action="store_true", help="print the JSON report instead of the table")

    arguments = parser.parse_args(argv)
    if arguments.command == "_manifest":
        return _cmd_manifest(arguments)

    root: Path = arguments.cases_root
    if not root.is_dir():
        parser.error(f"cases root is not a directory: {root}")
    for arm in (arguments.before, arguments.after):
        if refusal := src_refusal(arm):
            parser.error(refusal)
    cases = discover_cases(root)
    if arguments.only:
        wanted = set(arguments.only)
        cases = [case for case in cases if case.name in wanted]
        missing = sorted(wanted - {case.name for case in cases})
        if missing:
            parser.error(f"no case directory named {', '.join(missing)} under {root}")

    comparisons = compare_cases(cases, before=arguments.before, after=arguments.after)
    report = build_report(comparisons, before=arguments.before, after=arguments.after)
    print(json.dumps(report, indent=2) if arguments.json else render_text(report))
    return 1 if report["summary"]["unavailable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
