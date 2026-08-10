"""Claim extraction: the numbers an artifact reports, and where it reports them.

This is stage 1 and stage 2 of the claim pipeline — find every stated number,
then recognise when two statements are one claim. Resolution (which artifact
produced a number, and whether it agrees) is a separate layer that consumes
these.

Nothing in this package reads a verdict or writes a finding. It is additive:
the shipped drafting path is untouched until a caller opts into it, so a
repository's findings cannot move because extraction improved.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .candidates import (
    CandidateSource,
    ClaimCandidate,
    ClaimLocation,
    from_latex_prose,
    from_latex_tables,
    from_markdown_table,
)
from .cluster import ClaimCluster, certain, cluster_candidates
from .reconcile import matching_results

__all__ = [
    "CandidateSource",
    "ClaimCandidate",
    "ClaimCluster",
    "ClaimLocation",
    "certain",
    "cluster_candidates",
    "extract_candidates",
    "extract_claims",
    "from_latex_prose",
    "from_latex_tables",
    "from_markdown_table",
    "matching_results",
]

#: Documents whose tables state results, matched on basename at any depth, so
#: ``docs/results.md`` is read and ``docs/api/attention.md`` is not.
#:
#: This is a *cost* bound, not a precision one, and the measurement says so:
#: reading all 1,403 markdown files of ``transformers`` instead of its one
#: README yields the same zero candidates, because the metric-header
#: requirement in :func:`~adduce.claims.candidates.from_markdown_table` already
#: rejects all 2,351 numeric cells in that tree. Keeping the scope narrow buys
#: 1 read instead of 1,403; it is not what makes the result honest.
_CLAIM_DOCUMENTS = ("readme.md", "results.md", "benchmarks.md", "benchmark.md", "leaderboard.md")


def _claim_documents(repo: Repo) -> list[str]:
    return sorted(str(entry.path) for entry in repo.find_names(*_CLAIM_DOCUMENTS))


def extract_candidates(evidence: Evidence) -> list[ClaimCandidate]:
    """Every number this artifact states as a result, from every known source.

    Exhaustive by design. The caller decides what to present; this layer never
    truncates, because a dropped claim is indistinguishable from a claim that
    was never made.
    """
    candidates: list[ClaimCandidate] = []
    candidates.extend(from_latex_tables(evidence.latex.table_cells))
    candidates.extend(from_latex_prose(evidence.latex.metrics))
    repo: Repo = evidence.repo
    for path in _claim_documents(repo):
        text = repo.read_text(path)
        if text:
            candidates.extend(from_markdown_table(text, path))
    return candidates


def extract_claims(evidence: Evidence) -> list[ClaimCluster]:
    """Extraction and clustering together — the normal entry point.

    Ordered by where the artifact first states each claim, so the numbering an
    author is asked to confirm follows the document rather than the alphabet.
    """
    clusters = cluster_candidates(extract_candidates(evidence))
    clusters.sort(key=lambda c: (_primary(c).path, _primary(c).line, c.metric, c.value))
    return clusters


def _primary(cluster: ClaimCluster) -> ClaimLocation:
    """The earliest place the artifact states this claim."""
    return min(
        (member.location for member in cluster.members),
        key=lambda location: (location.path, location.line),
    )
