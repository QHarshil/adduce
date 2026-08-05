"""Coordinator provenance facts recomputed from a checked-out tree."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from corpus.scripts.review_facts import (
    COORDINATOR_ONLY_NOTICE,
    ReviewFactsError,
    collect_facts,
    main,
    parse_runbook_metadata,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAIR = ["pilot-test-a", "pilot-test-b"]
OTHER_PAIR = ["pilot-test-c", "pilot-test-d"]
REPOSITORY_IDS = ("alpha", "beta", "gamma")
CLONE_RELATIVE = "corpus/clones/pilot-2026-07-13"
TRUTH_RELATIVE = "corpus/labels/pilot-claims.json"
REPOS_RELATIVE = "corpus/repos.csv"
PREREGISTRATION_RELATIVE = "corpus/pilot-r6-preregistration.json"
SCAFFOLD_RELATIVE = (
    "corpus/labels/pilot-claim-review-r6-reviewer-a.json",
    "corpus/labels/pilot-claim-review-r6-reviewer-b.json",
)

TOP_LEVEL_KEYS = {
    "analyzer_source_tree_sha256",
    "candidate_pair",
    "coordinator_only",
    "corpus",
    "derived_at",
    "git",
    "notice",
    "package_version",
    "preregistration",
    "review_facts_schema_version",
    "root",
    "scaffolds",
    "scaffolds_byte_equal",
    "truth",
    "unavailable_inputs",
}
GIT_KEYS = {"available", "branch", "commit", "dirty"}
TRUTH_KEYS = {
    "available",
    "bytes",
    "claim_count",
    "corpus_inventory_sha256",
    "expected_resolution_counts",
    "expected_trail_status_counts",
    "frozen_at",
    "link_count",
    "path",
    "sha256",
    "unavailable_repositories",
}
CORPUS_KEYS = {
    "clones_available",
    "clones_path",
    "inventory_available",
    "inventory_path",
    "inventory_sha256",
    "repository_count",
    "unavailable_repositories",
}
SCAFFOLD_KEYS = {"available", "bytes", "candidate_pair", "path", "sha256"}
PREREGISTRATION_KEYS = {"available", "bytes", "candidate_pair", "path", "protocol_id", "sha256"}


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_json(path: Path, payload: Any) -> None:
    _write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _claim(claim_id: str, trail_status: str, resolutions: list[str]) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "repo_id": claim_id.split(".")[0],
        "repo_commit": "0" * 40,
        "expected_trail_status": trail_status,
        "expected_links": [
            {"target": f"target-{index}", "expected_resolution": resolution}
            for index, resolution in enumerate(resolutions)
        ],
    }


def _truth_payload() -> dict[str, Any]:
    return {
        "claim_ground_truth_schema_version": 1,
        "corpus_inventory_sha256": "a" * 64,
        "clone_manifest_sha256": "b" * 64,
        "frozen_at": "2026-01-02T03:04:05Z",
        "unavailable_repositories": [],
        "claims": [
            _claim("alpha.c1", "partial", ["resolved", "resolved", "unresolved"]),
            _claim("beta.c1", "complete", ["resolved", "not_applicable"]),
            _claim("gamma.c1", "partial", ["unresolved"]),
        ],
    }


def _scaffold_payload(pair: list[str]) -> dict[str, Any]:
    return {
        "claim_review_schema_version": 1,
        "candidate_pair": pair,
        "claim_ground_truth_sha256": "c" * 64,
        "claims": [],
    }


def _git(root: Path, *args: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Corpus Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Corpus Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
    )
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=environment)


def _build_root(root: Path, *, pairs: tuple[list[str], list[str]] = (PAIR, PAIR)) -> Path:
    _write_bytes(root / "src" / "adduce" / "__init__.py", b'__version__ = "9.9.9"\n')
    _write_bytes(root / "src" / "adduce" / "cli.py", b"VALUE = 1\n")
    rows = ["id,cohort,repo_url,commit_sha"]
    rows.extend(
        f"{identifier},badged_functional,https://example.invalid/{identifier},{'0' * 40}"
        for identifier in REPOSITORY_IDS
    )
    _write_bytes(root / REPOS_RELATIVE, ("\n".join(rows) + "\n").encode("utf-8"))
    for identifier in REPOSITORY_IDS[:-1]:
        (root / CLONE_RELATIVE / identifier).mkdir(parents=True, exist_ok=True)
    _write_json(root / TRUTH_RELATIVE, _truth_payload())
    for relative, pair in zip(SCAFFOLD_RELATIVE, pairs, strict=True):
        _write_json(root / relative, _scaffold_payload(pair))
    _write_json(
        root / PREREGISTRATION_RELATIVE,
        {
            "preregistration_schema_version": 1,
            "protocol_id": "pilot-test",
            "candidate_pair": PAIR,
        },
    )
    return root


def _initialise_git(root: Path) -> str:
    _git(root, "init", "--quiet", "--initial-branch", "review-fixture")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--message", "Freeze the review fixture")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return head.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _facts(root: Path) -> dict[str, Any]:
    return collect_facts(
        root=root,
        truth=root / TRUTH_RELATIVE,
        repos=root / REPOS_RELATIVE,
        clones=root / CLONE_RELATIVE,
        scaffolds=tuple(root / relative for relative in SCAFFOLD_RELATIVE),
        preregistration=root / PREREGISTRATION_RELATIVE,
    )


def _runbook(root: Path, commit: str, **overrides: Any) -> Path:
    fields: dict[str, Any] = {
        "source_commit": commit,
        "truth_sha256": _sha256(root / TRUTH_RELATIVE),
        "corpus_inventory_sha256": _sha256(root / REPOS_RELATIVE),
        "candidate_pair": PAIR,
        "preregistration_sha256": _sha256(root / PREREGISTRATION_RELATIVE),
        "derived_at": "2026-01-02T03:04:05Z",
    }
    fields.update(overrides)
    lines = [
        "# Gate 2 runbook",
        "",
        "```yaml",
        "review_runbook:",
        f"  source_commit: {fields['source_commit']}",
        f"  truth_sha256: {fields['truth_sha256']}",
        f"  corpus_inventory_sha256: {fields['corpus_inventory_sha256']}",
        "  candidate_pair:",
        *(f"    - {item}" for item in fields["candidate_pair"]),
        f"  preregistration_sha256: {fields['preregistration_sha256']}",
        f"  derived_at: {fields['derived_at']}",
        "```",
        "",
        "Coordinator notes follow.",
        "",
    ]
    path = root / "runbook.md"
    _write_bytes(path, "\n".join(lines).encode("utf-8"))
    return path


@pytest.fixture
def review_root(tmp_path: Path) -> Path:
    return _build_root(tmp_path / "repo")


def test_show_json_emits_exactly_the_documented_key_set(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["show", "--json", "--root", str(review_root)]) == 0
    facts = json.loads(capsys.readouterr().out)
    assert set(facts) == TOP_LEVEL_KEYS
    assert facts["review_facts_schema_version"] == 1
    assert facts["coordinator_only"] is True
    assert set(facts["git"]) == GIT_KEYS
    assert set(facts["truth"]) == TRUTH_KEYS
    assert set(facts["corpus"]) == CORPUS_KEYS
    assert set(facts["preregistration"]) == PREREGISTRATION_KEYS
    assert [set(record) for record in facts["scaffolds"]] == [SCAFFOLD_KEYS, SCAFFOLD_KEYS]


def test_counts_and_distributions_are_computed_from_the_truth_file(review_root: Path) -> None:
    truth = _facts(review_root)["truth"]
    assert truth["claim_count"] == 3
    assert truth["link_count"] == 6
    assert truth["expected_resolution_counts"] == {
        "not_applicable": 1,
        "resolved": 3,
        "unresolved": 2,
    }
    assert truth["expected_trail_status_counts"] == {"complete": 1, "partial": 2}
    assert truth["frozen_at"] == "2026-01-02T03:04:05Z"
    assert truth["sha256"] == _sha256(review_root / TRUTH_RELATIVE)


def test_repository_facts_report_the_inventory_digest_and_the_missing_clone(
    review_root: Path,
) -> None:
    facts = _facts(review_root)
    assert facts["package_version"] == "9.9.9"
    assert facts["corpus"]["inventory_sha256"] == _sha256(review_root / REPOS_RELATIVE)
    assert facts["corpus"]["repository_count"] == 3
    assert facts["corpus"]["unavailable_repositories"] == ["gamma"]
    assert facts["preregistration"]["protocol_id"] == "pilot-test"


def test_byte_equal_scaffolds_report_equal(review_root: Path) -> None:
    facts = _facts(review_root)
    digests = {record["sha256"] for record in facts["scaffolds"]}
    assert len(digests) == 1
    assert facts["scaffolds_byte_equal"] is True
    assert facts["candidate_pair"] == PAIR


def test_differing_scaffolds_report_unequal(review_root: Path) -> None:
    path = review_root / SCAFFOLD_RELATIVE[1]
    _write_bytes(path, path.read_bytes() + b"\n")
    facts = _facts(review_root)
    assert facts["scaffolds_byte_equal"] is False
    assert facts["candidate_pair"] == PAIR


def test_disagreeing_candidate_pairs_are_refused_and_both_values_named(tmp_path: Path) -> None:
    root = _build_root(tmp_path / "repo", pairs=(PAIR, OTHER_PAIR))
    with pytest.raises(ReviewFactsError) as error:
        _facts(root)
    message = str(error.value)
    assert "pilot-test-a + pilot-test-b" in message
    assert "pilot-test-c + pilot-test-d" in message


def test_a_missing_clone_root_is_reported_as_unavailable_by_name(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.rmtree(review_root / CLONE_RELATIVE)
    assert main(["show", "--root", str(review_root)]) == 0
    output = capsys.readouterr().out
    facts = _facts(review_root)
    assert facts["corpus"]["clones_available"] is False
    assert facts["corpus"]["unavailable_repositories"] is None
    assert facts["unavailable_inputs"] == [CLONE_RELATIVE]
    assert f"clone root: {CLONE_RELATIVE} (unavailable)" in output


def test_a_missing_truth_file_is_reported_as_unavailable_by_name(review_root: Path) -> None:
    (review_root / TRUTH_RELATIVE).unlink()
    facts = _facts(review_root)
    assert facts["truth"]["available"] is False
    assert facts["truth"]["claim_count"] is None
    assert TRUTH_RELATIVE in facts["unavailable_inputs"]


def test_verify_passes_on_matching_expectations(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "verify",
            "--root",
            str(review_root),
            "--expect-truth-sha256",
            _sha256(review_root / TRUTH_RELATIVE),
            "--expect-candidate-pair",
            PAIR[0],
            "--expect-candidate-pair",
            PAIR[1],
        ]
    )
    assert exit_code == 0
    assert "match the recorded expectations" in capsys.readouterr().out


def test_verify_exits_one_and_names_the_mismatched_field(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "verify",
            "--root",
            str(review_root),
            "--expect-truth-sha256",
            "d" * 64,
            "--expect-candidate-pair",
            OTHER_PAIR[0],
            "--expect-candidate-pair",
            OTHER_PAIR[1],
        ]
    )
    assert exit_code == 1
    output = capsys.readouterr().out
    assert f"truth_sha256: expected {'d' * 64}" in output
    assert "candidate_pair: expected pilot-test-c, pilot-test-d" in output
    assert "live pilot-test-a, pilot-test-b" in output


def test_check_runbook_accepts_a_correct_header(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit = _initialise_git(review_root)
    path = _runbook(review_root, commit)
    assert main(["check-runbook", "--root", str(review_root), "--path", str(path)]) == 0
    assert "matches the recomputed facts" in capsys.readouterr().out


def test_check_runbook_names_every_stale_field(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit = _initialise_git(review_root)
    path = _runbook(
        review_root,
        commit,
        source_commit="1" * 40,
        truth_sha256="2" * 64,
        corpus_inventory_sha256="3" * 64,
        candidate_pair=OTHER_PAIR,
        preregistration_sha256="4" * 64,
    )
    assert main(["check-runbook", "--root", str(review_root), "--path", str(path)]) == 1
    output = capsys.readouterr().out
    assert f"source_commit: recorded {'1' * 40}, live {commit}" in output
    assert f"truth_sha256: recorded {'2' * 64}" in output
    assert f"corpus_inventory_sha256: recorded {'3' * 64}" in output
    assert "candidate_pair: recorded pilot-test-c, pilot-test-d" in output
    assert f"preregistration_sha256: recorded {'4' * 64}" in output


def test_check_runbook_on_a_missing_file_reports_an_unusable_input_and_creates_nothing(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = review_root / "absent-runbook.md"
    exit_code = main(["check-runbook", "--root", str(review_root), "--path", str(missing)])
    assert exit_code == 2
    assert f"review runbook not found: {missing}" in capsys.readouterr().err
    assert not missing.exists()


def test_check_runbook_rejects_a_runbook_with_no_metadata_block(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = review_root / "runbook.md"
    _write_bytes(path, b"# Gate 2 runbook\n\nDerived against `dev` at some commit.\n")
    exit_code = main(["check-runbook", "--root", str(review_root), "--path", str(path)])
    assert exit_code == 2
    assert "no fenced yaml review_runbook metadata block" in capsys.readouterr().err


def test_check_runbook_rejects_a_malformed_metadata_block(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _runbook(review_root, "0" * 40, truth_sha256="short")
    exit_code = main(["check-runbook", "--root", str(review_root), "--path", str(path)])
    assert exit_code == 2
    assert "truth_sha256 is not a SHA-256 hex digest" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (["review_runbook:", "  source_commit: " + "0" * 40], "missing fields"),
        (
            ["review_runbook:", "  source_commit: " + "0" * 40, "  source_commit: " + "1" * 40],
            "duplicate runbook metadata field",
        ),
        (["review_runbook:", "  reviewer_name: someone"], "unknown runbook metadata field"),
        (["review_runbook:", "source_commit: " + "0" * 40], "unparsable line"),
        (["review_runbook:", "    - orphan"], "unexpected list item"),
    ],
)
def test_a_malformed_metadata_block_is_rejected_precisely(
    block: list[str], expected: str
) -> None:
    text = "\n".join(["```yaml", *block, "```", ""])
    with pytest.raises(ReviewFactsError, match=expected):
        parse_runbook_metadata(text)


def test_two_metadata_blocks_are_refused(review_root: Path) -> None:
    commit = "0" * 40
    path = _runbook(review_root, commit)
    duplicated = path.read_text(encoding="utf-8")
    with pytest.raises(ReviewFactsError, match="metadata blocks; expected one"):
        parse_runbook_metadata(duplicated + duplicated)


def test_markdown_states_that_the_report_is_coordinator_only(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["markdown", "--root", str(review_root)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("# Claim-review provenance facts")
    assert COORDINATOR_ONLY_NOTICE in output
    assert "Coordinator-only" in COORDINATOR_ONLY_NOTICE
    assert _sha256(review_root / TRUTH_RELATIVE) in output
    assert _sha256(review_root / PREREGISTRATION_RELATIVE) in output


def test_show_states_that_the_output_is_coordinator_only(
    review_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["show", "--root", str(review_root)]) == 0
    assert capsys.readouterr().out.startswith(COORDINATOR_ONLY_NOTICE)


def test_show_against_the_real_repository_reports_the_live_commit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not (REPOSITORY_ROOT / TRUTH_RELATIVE).is_file():
        pytest.skip("the frozen claim ground truth is not present in this checkout")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is unavailable in this checkout")
    assert main(["show", "--json", "--root", str(REPOSITORY_ROOT)]) == 0
    facts = json.loads(capsys.readouterr().out)
    assert facts["git"]["commit"] == head.stdout.strip()
    assert facts["truth"]["sha256"] == _sha256(REPOSITORY_ROOT / TRUTH_RELATIVE)
