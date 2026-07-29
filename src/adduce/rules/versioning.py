"""Versioning: is a specific, recoverable state of the code pinned down?"""

from __future__ import annotations

from ..evidence import Evidence
from .base import Category, Finding, Rule, Status

_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_commit_reference(value: str) -> bool:
    return 7 <= len(value) <= 40 and all(character in _HEX_DIGITS for character in value)


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
    title = "Tag marking the scanned revision"
    rationale = (
        "A tag pointing at the scanned commit gives reviewers a stable local name for that "
        "state. Static inspection does not establish that the tag was published or is immutable."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.git.is_repo:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.9, message="Not a git repository (see R-VER-001).")
        if ev.git.has_tags:
            return self.finding(
                Status.PASS,
                confidence=0.9,
                message="One or more local tags point at the current HEAD; publication was not verified.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No local tag points at the current HEAD.",
            remediation="Tag the exact commit used for the reported results and retain that tag.",
        )


class CommitReferenceRule(Rule):
    id = "R-VER-003"
    category = Category.VERSIONING
    title = "Exact revision referenced in README or manifest"
    rationale = (
        "A valid commit hash ties written instructions to a specific code state. Confirmed "
        "manifest references must identify the current checkout."
    )
    weight = 2

    def evaluate(self, ev: Evidence) -> Finding:
        manifest_commits: list[str] = []
        for claim in ev.manifest.claims:
            commit = claim.produced_by.commit
            if commit and (claim.status or "").strip().lower() == "confirmed":
                manifest_commits.append(commit.strip().lower())

        if manifest_commits:
            valid_commits = [commit for commit in manifest_commits if _is_commit_reference(commit)]
            invalid_count = len(manifest_commits) - len(valid_commits)
            head = (ev.git.head_commit or "").strip().lower()
            matching_count = (
                sum(head.startswith(commit) for commit in valid_commits)
                if _is_commit_reference(head)
                else 0
            )
            stale_count = len(valid_commits) - matching_count

            if matching_count == len(manifest_commits):
                return self.finding(
                    Status.PASS,
                    confidence=0.95,
                    message=(
                        f"The manifest pins {matching_count} confirmed claim(s) "
                        "to the current HEAD."
                    ),
                )

            counts = (
                f"matching={matching_count}; "
                f"stale_or_unverifiable={stale_count}; "
                f"invalid={invalid_count}."
            )
            if not valid_commits:
                return self.finding(
                    Status.FAIL,
                    confidence=0.95,
                    message=f"Confirmed manifest commit references are invalid: {counts}",
                    remediation=(
                        "Replace each confirmed claim's commit with a 7- to 40-character "
                        "hexadecimal Git commit that matches the checked-out HEAD."
                    ),
                )
            return self.finding(
                Status.PARTIAL,
                confidence=0.95,
                message=f"Confirmed manifest commit references do not all match this checkout: {counts}",
                remediation=(
                    "Check out the reported revision or update each confirmed claim to the "
                    "valid hexadecimal commit for the current HEAD."
                ),
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
