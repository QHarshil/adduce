"""A reviewer packet carries one reviewer's material and nothing that would unblind them."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from corpus.scripts import reviewer_packet
from corpus.scripts.claim_ground_truth import RESOLUTIONS, TARGETS
from corpus.scripts.claim_review import DECISIONS, initialize_review
from corpus.scripts.reviewer_packet import (
    EXPECTED_RESOLUTION_MAP_RE,
    FORBIDDEN_CONTENT_SENTINELS,
    MANIFEST_NAME,
    PACKET_ROLES,
    VERIFY_WRAPPER_SOURCE,
    scan_reviewer_document,
)
from corpus.scripts.run_contract import sha256_file, write_json
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
REVIEWER_ID = "reviewer-test-a"
CANDIDATE_PAIR = ["pilot-test-r6-a", "pilot-test-r6-b"]
CLAIM_REPOSITORIES = ("synthetic-repo-two", "synthetic-repo-one")
UNCLAIMED_REPOSITORY = "synthetic-repo-three"
REPOSITORY_IDS = (*CLAIM_REPOSITORIES, UNCLAIMED_REPOSITORY)
CLAIM_ID = f"{CLAIM_REPOSITORIES[0]}-claim"
FROZEN_CLOCK = datetime(2026, 7, 20, 9, 30, tzinfo=timezone.utc)

GUIDE_TEXT = """# Claim review — reviewer guide

Judge whether the frozen claim record is correct at the pinned commit.
Record a rationale and at least one evidence locator for every decision.

Verify the packet with:

```console
python -B corpus/scripts/reviewer_packet.py verify --packet <your-packet-dir>
```
"""
CHECKLIST_TEXT = """# Claim review — reviewer checklist

- [ ] The packet verifies.
- [ ] Every decision has a rationale and an evidence locator.
"""


def _clock() -> datetime:
    return FROZEN_CLOCK


def _git(*arguments: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _make_clone(path: Path) -> str:
    path.mkdir(parents=True)
    (path / "README.md").write_bytes(b"# Synthetic\n\nRun with `python train.py`.\n")
    (path / "train.py").write_bytes(b"print('synthetic')\n")
    _git("init", "-q", cwd=path)
    _git("config", "user.name", "Corpus Test", cwd=path)
    _git("config", "user.email", "corpus@example.invalid", cwd=path)
    _git("add", ".", cwd=path)
    _git("commit", "-qm", "synthetic", cwd=path)
    return _git("rev-parse", "HEAD", cwd=path)


def _write_repos(path: Path, commits: dict[str, str]) -> None:
    fieldnames = [
        "id",
        "cohort",
        "repo_url",
        "commit_sha",
        "badge_type",
        "venue",
        "year",
        "framework",
        "has_tex",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for repository_id in REPOSITORY_IDS:
            writer.writerow(
                {
                    "id": repository_id,
                    "cohort": "unvetted",
                    "repo_url": f"https://example.invalid/{repository_id}",
                    "commit_sha": commits[repository_id],
                    "framework": "python",
                    "has_tex": "false",
                }
            )


def _claim(clones: Path, repository_id: str, commit: str) -> dict[str, Any]:
    readme = clones / repository_id / "README.md"
    quote = readme.read_text(encoding="utf-8").splitlines()[2]
    links = []
    for target in TARGETS:
        if target == "reported_result":
            resolution, artifacts = "resolved", [{"kind": "claim_source"}]
        elif target == "commit":
            resolution, artifacts = "resolved", [{"kind": "literal", "value": commit}]
        else:
            resolution, artifacts = "not_applicable", []
        links.append(
            {
                "target": target,
                "expected_resolution": resolution,
                "artifacts": artifacts,
                "rationale": "The pinned source establishes this expected relationship.",
            }
        )
    return {
        "claim_id": f"{repository_id}-claim",
        "repo_id": repository_id,
        "repo_commit": commit,
        "source": {
            "kind": "repository_file",
            "path": "README.md",
            "sha256": sha256_file(readme),
            "line_start": 3,
            "line_end": 3,
            "quote": quote,
        },
        "claim": {
            "text": "python train.py",
            "metric": "documented command",
            "value": "python train.py",
        },
        "adduce_match": {"headline_contains": "python train.py"},
        "expected_trail_status": "supported",
        "expected_links": links,
        "ground_truth_review": {
            "prepared_by": "preparer-one",
            "prepared_at": "2026-07-13T22:00:00+00:00",
            "verified_by": "verifier-two",
            "verified_at": "2026-07-13T23:00:00+00:00",
        },
    }


def _truth(clones: Path, repos: Path, commits: dict[str, str]) -> dict[str, Any]:
    return {
        "claim_ground_truth_schema_version": 1,
        "corpus_inventory_sha256": sha256_file(repos),
        "clone_manifest_sha256": sha256_file(clones / "clones_manifest.json"),
        "frozen_at": "2026-07-14T00:00:00+00:00",
        "claims": [
            _claim(clones, repository_id, commits[repository_id])
            for repository_id in CLAIM_REPOSITORIES
        ],
        "unavailable_repositories": [],
    }


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Path]:
    """A synthetic corpus: two real clones, an inventory, a truth and an empty scaffold."""
    clones = tmp_path / "clones"
    clones.mkdir()
    commits = {
        repository_id: _make_clone(clones / repository_id) for repository_id in REPOSITORY_IDS
    }
    write_json(clones / "clones_manifest.json", {"records": sorted(commits)})
    repos = tmp_path / "repos.csv"
    _write_repos(repos, commits)
    claims = tmp_path / "pilot-claims.json"
    write_json(claims, _truth(clones, repos, commits))
    review = tmp_path / "scaffold.json"
    truth = json.loads(claims.read_text(encoding="utf-8"))
    write_json(review, initialize_review(truth, sha256_file(claims), list(CANDIDATE_PAIR)))
    guide = tmp_path / "REVIEWER_GUIDE.md"
    guide.write_bytes(GUIDE_TEXT.encode("utf-8"))
    checklist = tmp_path / "REVIEWER_CHECKLIST.md"
    checklist.write_bytes(CHECKLIST_TEXT.encode("utf-8"))
    return {
        "root": tmp_path,
        "clones": clones,
        "repos": repos,
        "claims": claims,
        "review": review,
        "guide": guide,
        "checklist": checklist,
        "workspace_schema": ROOT / "corpus" / "reviewer-workspace.schema.json",
        "review_schema": ROOT / "corpus" / "claim-review.schema.json",
    }


def _build_argv(corpus: dict[str, Path], out: Path, **overrides: Path) -> list[str]:
    paths = {
        "claims": corpus["claims"],
        "review": corpus["review"],
        "repos": corpus["repos"],
        "clones": corpus["clones"],
        "guide": corpus["guide"],
        "checklist": corpus["checklist"],
        "workspace-schema": corpus["workspace_schema"],
        "review-schema": corpus["review_schema"],
        **{key.replace("_", "-"): value for key, value in overrides.items()},
    }
    argv = ["build", "--reviewer-id", REVIEWER_ID, "--out", str(out)]
    for flag, value in paths.items():
        argv += [f"--{flag}", str(value)]
    return argv


def _build(corpus: dict[str, Path], out: Path, **overrides: Path) -> int:
    return reviewer_packet.main(_build_argv(corpus, out, **overrides), clock=_clock)


def _packet_bytes(packet: Path) -> dict[str, bytes]:
    return {
        entry.relative_to(packet).as_posix(): entry.read_bytes()
        for entry in sorted(packet.rglob("*"))
    }


def _retag(manifest_path: Path, mutation: dict[str, Any]) -> None:
    """Rewrite a manifest with a recomputed packet_id, leaving only *mutation* wrong."""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.update(mutation)
    body = {key: value for key, value in payload.items() if key != "packet_id"}
    payload["packet_id"] = reviewer_packet.packet_id(body)
    write_json(manifest_path, payload)


def test_build_writes_exactly_the_allowlisted_roles(corpus: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "packets" / "packet-a"
    assert _build(corpus, out) == 0
    assert set(_packet_bytes(out)) == {MANIFEST_NAME, *(name for name, _ in PACKET_ROLES)}
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["reviewer_id"] == REVIEWER_ID
    assert manifest["created_at"] == FROZEN_CLOCK.isoformat()
    assert manifest["candidate_pair"] == CANDIDATE_PAIR
    assert manifest["claim_ground_truth_sha256"] == sha256_file(corpus["claims"])
    assert manifest["review_scaffold_sha256"] == sha256_file(corpus["review"])
    assert [entry["role"] for entry in manifest["files"]] == [
        role for _, role in sorted(PACKET_ROLES)
    ]
    assert (out / "pilot-claims.json").read_bytes() == corpus["claims"].read_bytes()
    assert (out / "review-scaffold.json").read_bytes() == corpus["review"].read_bytes()


def test_manifest_conforms_to_the_published_packet_schema(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    schema = json.loads(
        (ROOT / "corpus" / "reviewer-packet.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest)
    )
    assert errors == []


def test_two_builds_with_the_same_clock_are_byte_identical(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    first = tmp_path / "packet-first"
    second = tmp_path / "packet-second"
    assert _build(corpus, first) == 0
    assert _build(corpus, second) == 0
    assert _packet_bytes(first) == _packet_bytes(second)


def test_a_later_clock_moves_only_the_creation_time_and_packet_id(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    first = tmp_path / "packet-first"
    second = tmp_path / "packet-second"
    assert _build(corpus, first) == 0
    later = datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)
    assert reviewer_packet.main(_build_argv(corpus, second), clock=lambda: later) == 0
    before = json.loads((first / MANIFEST_NAME).read_text(encoding="utf-8"))
    after = json.loads((second / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {key for key in before if before[key] != after[key]} == {"created_at", "packet_id"}
    assert _packet_bytes(first).keys() == _packet_bytes(second).keys()
    for name, data in _packet_bytes(first).items():
        assert name == MANIFEST_NAME or data == _packet_bytes(second)[name]


def test_verify_accepts_the_built_packet(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 0
    captured = capsys.readouterr().out
    assert "review packet verified: files=7 repositories=2 repository_bindings=checked" in captured


def test_verify_rejects_a_mutated_file(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    (out / "REVIEWER_GUIDE.md").write_bytes(GUIDE_TEXT.encode("utf-8") + b"\nEdited.\n")
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 2
    assert "REVIEWER_GUIDE.md hashes to" in capsys.readouterr().err


def test_verify_rejects_an_unexpected_extra_file(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    (out / "notes.md").write_bytes(b"stray\n")
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 2
    assert "unexpected=['notes.md']" in capsys.readouterr().err


def test_verify_rejects_a_deleted_file(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    (out / "REVIEWER_CHECKLIST.md").unlink()
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 2
    assert "missing=['REVIEWER_CHECKLIST.md']" in capsys.readouterr().err


def test_verify_rejects_a_manifest_that_renames_the_candidate_pair(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    _retag(out / MANIFEST_NAME, {"candidate_pair": ["pilot-test-r7-a", "pilot-test-r7-b"]})
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 2
    assert "different candidate pairs" in capsys.readouterr().err


def test_verify_rejects_a_manifest_with_a_forged_packet_id(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    payload = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    payload["created_at"] = "2026-07-25T00:00:00+00:00"
    write_json(out / MANIFEST_NAME, payload)
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 2
    assert "does not match the manifest body" in capsys.readouterr().err


def test_verify_passes_on_a_packet_copied_elsewhere(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    elsewhere = tmp_path / "handover" / "renamed-packet"
    elsewhere.parent.mkdir()
    elsewhere.mkdir()
    for name, data in _packet_bytes(out).items():
        (elsewhere / name).write_bytes(data)
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(elsewhere)]) == 0
    assert "repository_bindings=checked" in capsys.readouterr().out


def test_verify_reports_repository_bindings_unchecked_without_a_clone_root(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    _retag(out / MANIFEST_NAME, {"clone_root": "corpus/clones/absent-clone-root"})
    capsys.readouterr()
    assert reviewer_packet.main(["verify", "--packet", str(out)]) == 0
    assert "repository_bindings=unchecked" in capsys.readouterr().out


def test_build_refuses_a_scaffold_bound_to_a_different_truth(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffold = json.loads(corpus["review"].read_text(encoding="utf-8"))
    scaffold["claim_ground_truth_sha256"] = hashlib.sha256(b"other truth").hexdigest()
    write_json(corpus["review"], scaffold)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    assert "different candidate truth SHA-256" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_a_scaffold_whose_candidate_pair_differs_from_two_labels(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffold = json.loads(corpus["review"].read_text(encoding="utf-8"))
    scaffold["candidate_pair"] = [CANDIDATE_PAIR[0], CANDIDATE_PAIR[0]]
    write_json(corpus["review"], scaffold)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    assert "invalid candidate pair" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_a_non_empty_scaffold_naming_the_claim(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scaffold = json.loads(corpus["review"].read_text(encoding="utf-8"))
    scaffold["claims"][0]["reviews"] = [{"reviewer_id": "reviewer-test-b"}]
    write_json(corpus["review"], scaffold)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    message = capsys.readouterr().err
    assert f"already carries a review decision for claim {CLAIM_ID}" in message
    assert not out.exists()


def test_build_refuses_a_clone_at_the_wrong_commit_naming_both_commits(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = corpus["clones"] / "synthetic-repo-two"
    expected = _git("rev-parse", "HEAD", cwd=clone)
    (clone / "train.py").write_bytes(b"print('moved')\n")
    _git("add", ".", cwd=clone)
    _git("commit", "-qm", "moved", cwd=clone)
    moved = _git("rev-parse", "HEAD", cwd=clone)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    message = capsys.readouterr().err
    assert "synthetic-repo-two" in message
    assert moved in message
    assert expected in message
    assert not out.exists()


def test_a_repository_carrying_no_claim_is_not_bound(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    bound = {entry["repository_id"] for entry in manifest["repositories"]}
    inventory = {row["id"] for row in csv.DictReader(corpus["repos"].read_text().splitlines())}
    assert UNCLAIMED_REPOSITORY in inventory
    assert bound == set(CLAIM_REPOSITORIES)
    assert UNCLAIMED_REPOSITORY not in bound


def test_a_repository_carrying_no_claim_cannot_fail_the_build(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    clone = corpus["clones"] / UNCLAIMED_REPOSITORY
    (clone / "train.py").write_bytes(b"print('moved')\n")
    _git("add", ".", cwd=clone)
    _git("commit", "-qm", "moved", cwd=clone)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0


def test_build_refuses_a_claim_naming_a_repository_outside_the_inventory(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    truth = json.loads(corpus["claims"].read_text(encoding="utf-8"))
    truth["claims"][0]["repo_id"] = "synthetic-repo-absent"
    write_json(corpus["claims"], truth)
    review = json.loads(corpus["review"].read_text(encoding="utf-8"))
    review["claim_ground_truth_sha256"] = sha256_file(corpus["claims"])
    review["claims"][0]["repo_id"] = "synthetic-repo-absent"
    review["claims"][0]["claim_record_sha256"] = hashlib.sha256(
        json.dumps(
            truth["claims"][0], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    write_json(corpus["review"], review)
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    assert "absent from the inventory" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_an_absent_clone_root_rather_than_inventing_a_commit(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out, clones=tmp_path / "no-such-clone-root") == 2
    message = capsys.readouterr().err
    assert "is absent; no repository commit can be resolved" in message
    assert not out.exists()


def test_build_refuses_an_output_inside_the_clone_tree(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = corpus["clones"] / "packet-a"
    assert _build(corpus, out) == 2
    assert "must be outside immutable input" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_an_existing_output(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    out.mkdir()
    assert _build(corpus, out) == 2
    assert "refusing to overwrite existing packet" in capsys.readouterr().err
    assert list(out.iterdir()) == []


def test_build_refuses_a_symlinked_source(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    linked = tmp_path / "linked-guide.md"
    linked.symlink_to(corpus["guide"])
    out = tmp_path / "packet-a"
    assert _build(corpus, out, guide=linked) == 2
    assert "passes through symlink" in capsys.readouterr().err
    assert not out.exists()


def test_a_symlinked_ancestor_above_the_root_is_accepted(tmp_path: Path) -> None:
    real = tmp_path / "real-root"
    (real / "review").mkdir(parents=True)
    (real / "review" / "REVIEWER_GUIDE.md").write_bytes(GUIDE_TEXT.encode("utf-8"))
    via = tmp_path / "via"
    via.symlink_to(real)
    resolved = reviewer_packet.require_regular_source(
        via / "review" / "REVIEWER_GUIDE.md", "guide", via
    )
    assert resolved.read_bytes() == GUIDE_TEXT.encode("utf-8")


def test_a_symlinked_component_below_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "review").mkdir(parents=True)
    (root / "review" / "REVIEWER_GUIDE.md").write_bytes(GUIDE_TEXT.encode("utf-8"))
    planted = tmp_path / "elsewhere"
    planted.mkdir()
    (planted / "REVIEWER_GUIDE.md").write_bytes(b"# planted\n")
    (root / "redirected").symlink_to(planted)
    with pytest.raises(reviewer_packet.ReviewerPacketError, match="passes through symlink"):
        reviewer_packet.require_regular_source(
            root / "redirected" / "REVIEWER_GUIDE.md", "guide", root
        )
    with pytest.raises(reviewer_packet.ReviewerPacketError, match="passes through symlink"):
        (root / "review" / "LINKED_GUIDE.md").symlink_to(planted / "REVIEWER_GUIDE.md")
        reviewer_packet.require_regular_source(
            root / "review" / "LINKED_GUIDE.md", "guide", root
        )


def test_build_refuses_a_path_traversal_output(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packets" / ".." / "escaped-packet"
    assert _build(corpus, out) == 2
    assert "parent-directory component" in capsys.readouterr().err
    assert not (tmp_path / "escaped-packet").exists()


@pytest.mark.parametrize("sentinel", [sentinel for sentinel, _ in FORBIDDEN_CONTENT_SENTINELS])
def test_build_refuses_a_reviewer_document_carrying_a_sentinel(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], sentinel: str
) -> None:
    corpus["guide"].write_bytes(f"{GUIDE_TEXT}\nSee {sentinel} for context.\n".encode())
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    assert repr(sentinel) in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_a_reviewer_document_carrying_an_expected_resolution_map(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus["checklist"].write_bytes(f"{CHECKLIST_TEXT}\nR U N R R N U R R N\n".encode())
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 2
    assert "compact expected-resolution map" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_a_withheld_source_path(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    withheld = tmp_path / "pilot-claim-links-r2-a.json"
    withheld.write_bytes(corpus["claims"].read_bytes())
    out = tmp_path / "packet-a"
    assert _build(corpus, out, claims=withheld) == 2
    assert "'pilot-claim-links'" in capsys.readouterr().err
    assert not out.exists()


def test_build_refuses_another_reviewers_scaffold(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "pilot-claim-review-reviewer-test-b.json"
    other.write_bytes(corpus["review"].read_bytes())
    out = tmp_path / "packet-a"
    assert _build(corpus, out, review=other) == 2
    message = capsys.readouterr().err
    assert "belongs to reviewer-test-b" in message
    assert not out.exists()


def test_build_accepts_a_scaffold_named_for_this_reviewer(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    mine = tmp_path / f"pilot-claim-review-{REVIEWER_ID}.json"
    mine.write_bytes(corpus["review"].read_bytes())
    out = tmp_path / "packet-a"
    assert _build(corpus, out, review=mine) == 0


def test_a_prevalidation_failure_creates_nothing_beside_the_output(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    out = tmp_path / "packets" / "packet-a"
    assert _build(corpus, out, clones=tmp_path / "no-such-clone-root") == 2
    assert not out.exists()
    assert not out.parent.exists()


def test_a_failed_materialisation_leaves_no_temporary_directory(
    corpus: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(path: Path, payload: Any) -> None:
        raise reviewer_packet.RunContractError("manifest write refused by the test")

    monkeypatch.setattr(reviewer_packet, "write_json", _refuse)
    out = tmp_path / "packets" / "packet-a"
    assert _build(corpus, out) == 2
    assert not out.exists()
    assert list(out.parent.iterdir()) == []


def test_repositories_and_files_are_sorted(corpus: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    repositories = [entry["repository_id"] for entry in manifest["repositories"]]
    assert repositories == sorted(CLAIM_REPOSITORIES)
    assert repositories != list(CLAIM_REPOSITORIES)
    assert [entry["path"] for entry in manifest["files"]] == sorted(
        name for name, _ in PACKET_ROLES
    )
    assert all(entry["read_only_expected"] is True for entry in manifest["repositories"])


def test_inspect_prints_bindings_without_an_expected_resolution_or_aggregate(
    corpus: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    capsys.readouterr()
    assert reviewer_packet.main(["inspect", "--packet", str(out)]) == 0
    printed = capsys.readouterr().out
    assert f"reviewer_id: {REVIEWER_ID}" in printed
    assert "repositories: 2" in printed
    assert "files: 7" in printed
    assert "repository_bindings: checked" in printed
    for forbidden in {*RESOLUTIONS, *DECISIONS}:
        assert forbidden not in printed
    assert EXPECTED_RESOLUTION_MAP_RE.search(printed) is None
    assert [line.split(":", 1)[0] for line in printed.splitlines()] == [
        "packet_id",
        "reviewer_id",
        "source_commit",
        "claim_ground_truth_sha256",
        "candidate_pair",
        "repositories",
        "files",
        "repository_bindings",
        "verification",
    ]


def test_inspect_reports_an_unavailable_source_commit_rather_than_inventing_one(
    tmp_path: Path,
) -> None:
    assert reviewer_packet.source_commit(tmp_path) is None
    if not (ROOT / ".git").exists():
        # The source distribution ships this suite, and a downstream
        # re-packager runs it from an unpacked archive. The assertion above is
        # the product behaviour; the one below needs a git checkout to have an
        # answer to compare against, and `git rev-parse` in a non-repository
        # would fail the test for the absence of git rather than for a defect.
        pytest.skip("no git checkout: the source distribution has no repository to query")
    assert reviewer_packet.source_commit(ROOT) == _git("rev-parse", "HEAD", cwd=ROOT)


def test_the_generated_wrapper_verifies_a_packet_from_inside_it(
    corpus: dict[str, Path], tmp_path: Path
) -> None:
    out = tmp_path / "packet-a"
    assert _build(corpus, out) == 0
    completed = subprocess.run(
        [sys.executable, "-B", "verify_packet.py"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "review packet verified: files=7 repositories=2" in completed.stdout
    (out / "review-scaffold.json").write_bytes(b"{}\n")
    refused = subprocess.run(
        [sys.executable, "-B", "verify_packet.py"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "review-scaffold.json" in refused.stderr


def test_the_wrapper_declares_the_same_roles_as_the_builder() -> None:
    for name, role in PACKET_ROLES:
        assert f'"{name}": "{role}"' in VERIFY_WRAPPER_SOURCE


def test_the_tracked_reviewer_documents_carry_no_forbidden_marker() -> None:
    for name in ("REVIEWER_GUIDE.md", "REVIEWER_CHECKLIST.md"):
        scan_reviewer_document((ROOT / "corpus" / "review" / name).read_bytes(), name)
