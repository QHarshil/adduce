"""Trust boundaries for version tags and manifest commit references."""

from __future__ import annotations

import subprocess
from pathlib import Path

from adduce.evidence.git import GitEvidence
from adduce.manifest import Claim, ProducedBy
from adduce.model import scan_repository
from adduce.rules.base import Status
from adduce.rules.versioning import CommitReferenceRule, TaggedReleaseRule


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "user.name=Adduce Test",
        "commit",
        "-qm",
        message,
    )
    return _git(root, "rev-parse", "HEAD")


def test_only_tags_pointing_at_head_are_collected(tmp_path):
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "train.py"
    tracked.write_text("print('first')\n", encoding="utf-8")
    _commit(tmp_path, "first")
    _git(tmp_path, "tag", "v-old")

    tracked.write_text("print('second')\n", encoding="utf-8")
    _commit(tmp_path, "second")

    repository = scan_repository(tmp_path)
    assert repository.git.tags == ()

    _git(tmp_path, "tag", "v-current")
    repository = scan_repository(tmp_path)
    assert repository.git.tags == ("v-current",)


def test_tag_rule_does_not_treat_old_tag_as_current_state(tmp_path, make_evidence):
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "train.py"
    tracked.write_text("print('first')\n", encoding="utf-8")
    _commit(tmp_path, "first")
    _git(tmp_path, "tag", "v-old")
    tracked.write_text("print('second')\n", encoding="utf-8")
    _commit(tmp_path, "second")

    evidence = make_evidence({})
    finding = TaggedReleaseRule().evaluate(evidence)

    assert finding.status is Status.FAIL
    assert "current HEAD" in finding.message


def test_confirmed_manifest_commit_must_match_current_head(make_evidence):
    head = "a" * 40
    evidence = make_evidence({"train.py": "pass\n"})
    evidence.git = GitEvidence(is_repo=True, head_commit=head)
    evidence.manifest.claims = [
        Claim(
            id="C1",
            produced_by=ProducedBy(commit=head[:12]),
            status="confirmed",
        )
    ]

    finding = CommitReferenceRule().evaluate(evidence)

    assert finding.status is Status.PASS
    assert "current HEAD" in finding.message


def test_invalid_confirmed_manifest_commit_fails(make_evidence):
    evidence = make_evidence({"train.py": "pass\n"})
    evidence.git = GitEvidence(is_repo=True, head_commit="a" * 40)
    evidence.manifest.claims = [
        Claim(
            id="C1",
            produced_by=ProducedBy(commit="not-a-commit"),
            status="confirmed",
        )
    ]

    finding = CommitReferenceRule().evaluate(evidence)

    assert finding.status is Status.FAIL
    assert "invalid=1" in finding.message


def test_stale_confirmed_manifest_commit_is_partial(make_evidence):
    evidence = make_evidence({"train.py": "pass\n"})
    evidence.git = GitEvidence(is_repo=True, head_commit="a" * 40)
    evidence.manifest.claims = [
        Claim(
            id="C1",
            produced_by=ProducedBy(commit="b" * 40),
            status="confirmed",
        )
    ]

    finding = CommitReferenceRule().evaluate(evidence)

    assert finding.status is Status.PARTIAL
    assert "do not all match this checkout" in finding.message


def test_draft_manifest_commit_is_not_commit_evidence(make_evidence):
    evidence = make_evidence({"train.py": "pass\n"})
    evidence.git = GitEvidence(is_repo=True, head_commit="a" * 40)
    evidence.manifest.claims = [
        Claim(
            id="C1",
            produced_by=ProducedBy(commit="a" * 12),
            status="draft",
        )
    ]

    finding = CommitReferenceRule().evaluate(evidence)

    assert finding.status is Status.FAIL
    assert finding.message == "No specific revision referenced in the README or manifest."
