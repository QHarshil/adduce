"""Manifest-level byte identity: discovery, non-destructiveness, and liveness.

The integration fixtures run the real harness over the real synthetic corpus
through subprocesses, exactly as a worker comparing two trees would. The
liveness fixture is the point of the instrument and is written as a mutation:
it patches a copy of ``src`` so that only a claim's confidence and resolution
method can move, and asserts the harness sees it. The default JSON report
carries neither field, so nothing built on that report can.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from bench.dev import manifest_identity

_CASES_ROOT = manifest_identity.DEFAULT_CASES_ROOT
_QUOTED_BASELINES = "synthetic_quoted_baseline_rows"
_CITED_ROW_LABEL = "synthetic_metric_unrelated_table"
_CONTROL_CASE = "synthetic_rounding_match"
_AUTHOR_MANIFEST_CASE = "synthetic_hydra_authority"

#: The one line that decides whether a cell the collector saw attributed to
#: prior work keeps full confidence. Removing the demotion moves a claim's
#: ``confidence`` and ``resolution_method`` and nothing else whatsoever.
_DEMOTION = "certain = named_by_header and not cell.prior_work"
_NO_DEMOTION = "certain = named_by_header"


def _inventory(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _git_status() -> str | None:
    """The working tree's status, or ``None`` where git cannot answer.

    Compared before and against after rather than asserted empty: this tree
    carries unrelated uncommitted work, and what matters is that the harness
    added nothing to it.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=manifest_identity._REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _copy_src(destination: Path) -> Path:
    shutil.copytree(
        manifest_identity.DEFAULT_SRC,
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return destination


# -- discovery ----------------------------------------------------------------


def test_discovery_finds_every_case_directory_and_nothing_else():
    """The case set is whatever is on disk, and it is directories only.

    ``corpus/synthetic`` also holds ``expectations.yaml``, whose entries
    ``tests/test_synthetic_corpus.py`` already pins against this same directory
    set. Measured while this was last checked: 33 case directories.
    """
    discovered = manifest_identity.discover_cases(_CASES_ROOT)
    on_disk = {path for path in _CASES_ROOT.iterdir() if path.is_dir()}

    assert set(discovered) == on_disk
    assert discovered == sorted(discovered)
    assert (_CASES_ROOT / "expectations.yaml").is_file()
    assert all(path.is_dir() for path in discovered)


def test_discovery_skips_dot_directories_and_bytecode_caches(tmp_path):
    (tmp_path / "case_one").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "expectations.yaml").write_text("cases: []\n", encoding="utf-8")

    assert manifest_identity.discover_cases(tmp_path) == [tmp_path / "case_one"]


# -- the field diff, without paying for a subprocess --------------------------


def _measured(claims: list[dict[str, object]], digest: str = "aaaa") -> manifest_identity.CaseManifest:
    return manifest_identity.CaseManifest(
        available=True, digest=digest, mode="scaffold", claims=tuple(claims)
    )


def test_a_confidence_only_difference_is_reported_as_that_and_counted():
    comparison = manifest_identity.CaseComparison(
        case_id="case",
        before=_measured([{"id": "C1", "value": 1.0, "confidence": 1.0}], digest="aaaa"),
        after=_measured([{"id": "C1", "value": 1.0, "confidence": 0.5}], digest="bbbb"),
    )

    assert comparison.identical is False
    assert comparison.moved_fields == ("confidence",)
    assert comparison.moved_claims == 1
    assert comparison.summary() == "moved (confidence on 1 of 1 claim(s))"


def test_a_changed_claim_count_reports_the_counts_rather_than_a_field_diff():
    """Positional pairing is void once the claim list changes length.

    The manifest numbers claims ``C1``..``Cn`` in extraction order, so a
    dropped claim renumbers every later one. The first claim is the one dropped
    here deliberately: pairing by position would then read ``C1`` against what
    used to be ``C2``, and report a moved value on a claim that never changed.
    """
    comparison = manifest_identity.CaseComparison(
        case_id="case",
        before=_measured(
            [{"id": "C1", "value": 1.0}, {"id": "C2", "value": 2.0}], digest="aaaa"
        ),
        after=_measured([{"id": "C1", "value": 2.0}], digest="bbbb"),
    )

    assert comparison.moved_fields == ()
    assert comparison.moved_claims == 0
    assert comparison.summary() == "moved (claims 2 -> 1)"


def test_a_move_no_claim_field_explains_says_where_it_is_not():
    """The digest covers the whole manifest, not only its claims.

    A dataset, environment or smoke entry that moved leaves every claim
    identical, so the summary has to say the difference is elsewhere rather than
    report nothing.
    """
    claims = [{"id": "C1", "value": 1.0}]
    comparison = manifest_identity.CaseComparison(
        case_id="case",
        before=_measured(claims, digest="aaaa"),
        after=_measured(claims, digest="bbbb"),
    )

    assert comparison.identical is False
    assert comparison.moved_fields == ()
    assert comparison.summary() == (
        "moved (no claim field: the difference is elsewhere in the manifest)"
    )


def test_a_case_whose_drafting_branch_changed_has_moved_whatever_its_digest_says():
    before = manifest_identity.CaseManifest(
        available=True, digest="aaaa", mode="scaffold", claims=()
    )
    after = manifest_identity.CaseManifest(
        available=True, digest="aaaa", mode="refresh_proposal", claims=()
    )
    comparison = manifest_identity.CaseComparison(case_id="case", before=before, after=after)

    assert comparison.identical is False
    assert comparison.summary() == "moved (mode scaffold -> refresh_proposal)"


def test_a_failed_worker_reports_its_own_last_line_as_the_reason():
    measured = manifest_identity._case_manifest_from_json(
        {
            "available": False,
            "reason": "manifest worker exited 1",
            "stderr_tail": "Traceback (most recent call last):\n  ...\nSafeWriteError: refused\n",
        }
    )

    assert measured.available is False
    assert measured.reason == "manifest worker exited 1: SafeWriteError: refused"


def test_an_unmeasurable_case_is_reported_unavailable_and_never_identical(tmp_path):
    measured = manifest_identity.measure_case(tmp_path / "absent", src=None)

    assert measured.available is False
    assert measured.reason is not None and "absent" in measured.reason

    comparison = manifest_identity.CaseComparison(
        case_id="absent", before=measured, after=measured
    )
    assert comparison.identical is False
    assert comparison.summary().startswith("unavailable:")


# -- the real corpus ----------------------------------------------------------


def test_one_tree_against_itself_is_identical_and_says_it_measured_nothing(capsys):
    exit_code = manifest_identity.main(
        ["compare", "--only", _QUOTED_BASELINES, "--only", _CONTROL_CASE]
    )
    rendered = capsys.readouterr().out

    assert exit_code == 0
    assert "2 case(s): 2 identical, 0 moved, 0 unavailable" in rendered
    assert "this comparison is vacuous" in rendered


def test_the_author_manifest_case_is_drafted_beside_the_authors_file(capsys):
    """``synthetic_hydra_authority`` ships a confirmed manifest.

    The CLI refuses to overwrite it and drafts a proposal under ``--refresh``
    instead, so the harness takes that branch for this case. Reported, because a
    case that stopped taking it would be measuring something else.
    """
    exit_code = manifest_identity.main(["compare", "--only", _AUTHOR_MANIFEST_CASE, "--json"])
    report = capsys.readouterr().out

    assert exit_code == 0
    assert '"before_mode": "refresh_proposal"' in report
    assert '"after_mode": "refresh_proposal"' in report
    assert '"identical": true' in report


def test_measuring_the_corpus_leaves_every_tracked_case_byte_identical(capsys):
    """``adduce manifest`` writes, and ``corpus/synthetic`` is tracked.

    The harness copies each case out of the repository before drafting
    anything, so no ``.adduce`` directory may appear under a case and the one
    case that already has a manifest must still hold the author's bytes.
    """
    before_inventory = _inventory(_CASES_ROOT)
    before_status = _git_status()
    if before_status is None:
        pytest.skip("git cannot report this tree's status")

    manifest_identity.main(
        ["compare", "--only", _QUOTED_BASELINES, "--only", _AUTHOR_MANIFEST_CASE]
    )
    capsys.readouterr()

    assert _inventory(_CASES_ROOT) == before_inventory
    assert not (_CASES_ROOT / _QUOTED_BASELINES / ".adduce").exists()

    after_status = _git_status()
    assert after_status == before_status


# -- liveness: the whole reason the instrument exists -------------------------


def test_a_confidence_only_change_moves_the_case_that_carries_the_signal(tmp_path):
    """Proof that this harness sees what the JSON-report check cannot.

    Both arms are snapshots of ``src`` taken together, so nothing else can
    differ between them, and the second has the baseline demotion removed --
    a change that can only move ``confidence`` and ``resolution_method``.

    Two cases carry the signal and both are driven here, because they carry it
    by different markup. ``synthetic_quoted_baseline_rows`` marks two of its
    five rows with a citation command, so four of its ten cells are demoted and
    six are read at full confidence either way. ``synthetic_metric_unrelated_
    table`` names its prior work in plain text -- ``ResNet-50 (He et al.)`` --
    and every claim it drafts sits in that row, so it moves on all of them.
    Both are named rather than counted for, so a corpus that gains a third
    carrier does not quietly change what this asserts.

    The control drafts a claim of its own and carries no attributed row, so it
    must stay identical across the same mutation. A control that drafted
    nothing would hold whatever the mutation did.
    """
    before_src = _copy_src(tmp_path / "before" / "src")
    after_src = _copy_src(tmp_path / "after" / "src")
    patched = after_src / "adduce" / "claims" / "candidates.py"
    source = patched.read_text(encoding="utf-8")
    assert source.count(_DEMOTION) == 1, "the demotion this mutation removes has moved or gone"
    patched.write_text(source.replace(_DEMOTION, _NO_DEMOTION), encoding="utf-8")

    comparisons = manifest_identity.compare_cases(
        [
            _CASES_ROOT / _QUOTED_BASELINES,
            _CASES_ROOT / _CITED_ROW_LABEL,
            _CASES_ROOT / _CONTROL_CASE,
        ],
        before=before_src,
        after=after_src,
    )
    by_case = {comparison.case_id: comparison for comparison in comparisons}
    moved = by_case[_QUOTED_BASELINES]

    assert moved.before.loaded_from != moved.after.loaded_from
    assert moved.identical is False
    assert moved.moved_fields == ("confidence", "resolution_method")
    assert moved.moved_claims == 4
    assert len(moved.before.claims) == len(moved.after.claims) == 10

    plain_text = by_case[_CITED_ROW_LABEL]
    assert plain_text.identical is False
    assert plain_text.moved_fields == ("confidence", "resolution_method")
    assert plain_text.moved_claims == len(plain_text.before.claims) == 2

    assert by_case[_CONTROL_CASE].identical is True

    demoted = [claim for claim in moved.before.claims if claim.get("confidence") == 0.5]
    assert len(demoted) == 4
    assert all(claim.get("confidence") == 1.0 for claim in moved.after.claims)


def test_the_report_states_which_tree_each_arm_loaded(tmp_path):
    before_src = _copy_src(tmp_path / "before" / "src")
    comparisons = manifest_identity.compare_cases(
        [_CASES_ROOT / _CONTROL_CASE], before=before_src, after=None
    )
    report = manifest_identity.build_report(comparisons, before=before_src, after=None)

    assert report["arms"]["before_loaded_from"] == str(before_src / "adduce")
    assert report["arms"]["after_loaded_from"] == str(manifest_identity.DEFAULT_SRC / "adduce")
    assert report["arms"]["arms_loaded_the_same_tree"] is False


def test_an_unknown_case_id_is_refused_rather_than_silently_measuring_nothing():
    with pytest.raises(SystemExit):
        manifest_identity.main(["compare", "--only", "synthetic_not_a_case"])


def test_a_tree_with_no_adduce_package_is_refused_rather_than_measured(tmp_path):
    """An empty ``--src`` would not fail: the import falls through to the install.

    Both arms would then resolve this repository's own tree, and a comparison
    against one bogus arm would read as a difference between two trees.
    """
    empty = tmp_path / "not-a-tree"
    empty.mkdir()

    assert manifest_identity.src_refusal(empty) == f"no adduce package under {empty}"
    assert manifest_identity.src_refusal(manifest_identity.DEFAULT_SRC) is None

    measured = manifest_identity.measure_case(_CASES_ROOT / _CONTROL_CASE, src=empty)
    assert measured.available is False
    assert measured.loaded_from is None

    with pytest.raises(SystemExit):
        manifest_identity.main(
            ["compare", "--only", _CONTROL_CASE, "--after", str(empty)]
        )


def test_a_case_that_cannot_be_measured_makes_the_run_exit_non_zero(monkeypatch, capsys):
    """A case the worker could not draft is a failed run, not a clean pass."""
    monkeypatch.setattr(
        manifest_identity,
        "measure_case",
        lambda case, *, src=None: manifest_identity.CaseManifest(
            available=False, reason="worker refused"
        ),
    )
    exit_code = manifest_identity.main(["compare", "--only", _CONTROL_CASE])
    rendered = capsys.readouterr().out

    assert exit_code == 1
    assert "1 unavailable" in rendered
    assert "unavailable: worker refused" in rendered
