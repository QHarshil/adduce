"""Versioning: is a specific, recoverable state of the code pinned down?"""

from __future__ import annotations

from ..evidence import Evidence
from .base import Category, Finding, Rule, Status


class GitRepositoryRule(Rule):
    id = "R-VER-001"
    category = Category.VERSIONING
    title = "Under version control"
    rationale = "Results belong to a commit, not a directory; without git there is no commit."
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.git.is_repo:
            return self.finding(Status.PASS, confidence=0.95, message="The directory is a git repository.")
        return self.finding(
            Status.FAIL,
            confidence=0.95,
            message="The directory is not a git repository.",
            remediation="Initialise git and publish the repository; tag the state used for reported results.",
        )


class TaggedReleaseRule(Rule):
    id = "R-VER-002"
    category = Category.VERSIONING
    title = "Tagged release marking the reported state"
    rationale = "Tags make the exact state that produced the paper recoverable years later."
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.git.is_repo:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.9, message="Not a git repository (see R-VER-001).")
        if ev.git.has_tags:
            return self.finding(Status.PASS, confidence=0.9, message="Tagged release(s) present.")
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No tags: the state that produced the results is not marked.",
            remediation="Tag the exact commit used for the paper (e.g. `git tag v1.0-paper && git push --tags`).",
        )


class CommitReferenceRule(Rule):
    id = "R-VER-003"
    category = Category.VERSIONING
    title = "Exact revision referenced in README or manifest"
    rationale = (
        "A commit hash in the docs ties the written instructions to the code state they "
        "were written for."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        manifest_commits = [c.produced_by.commit for c in ev.manifest.claims if c.produced_by.commit]
        if manifest_commits:
            return self.finding(
                Status.PASS, confidence=0.9, message=f"The manifest pins commits for {len(manifest_commits)} claim(s)."
            )
        if ev.git.commit_referenced_in_readme:
            return self.finding(Status.PASS, confidence=0.7, message="The README references a specific commit or revision.")
        if ev.git.has_tags:
            return self.finding(
                Status.PARTIAL,
                confidence=0.6,
                message="Tags exist but neither README nor manifest says which revision reproduces the reported results.",
                remediation="State in the README (or per-claim in the manifest) which tag or commit produced the results.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="No specific revision referenced in the README or manifest.",
            remediation="Reference the exact tag or commit (e.g. 'results were produced at commit abc1234').",
        )
