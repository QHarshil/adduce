"""Stage 2: the same claim stated twice is one claim.

A paper states its headline number in the abstract, again in a results table,
and often a third time in the repository README. Those are three statements of
one claim, and treating them as three claims inflates every count downstream —
including the coverage figure the author is asked to act on.

Clustering replaces the ``[:10]`` truncation it inherits. Truncation dropped
claims without saying so; clustering drops nothing and every member keeps its
own location, so an author can be shown all three places a number appears.

The premise is restatement, never coincidence. Two cells of a results table
report two measurements side by side, and one of them rounding to the other does
not make them one statement, so a candidate that names what it measured -- its
row and its column -- joins only a claim naming the same thing.

Rounding is handled deliberately: a paper writing ``92.4`` and a log writing
``92.41`` are agreeing, so members are compared at the precision of the *less*
precise one. Cross-unit reconciliation (``0.924`` against ``92.4``) is
explicitly not done here — that is numeric reconciliation, a later stage with
its own resolution method, and doing it silently at clustering time would let
an inference masquerade as a parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..aeg.schema import CERTAIN_METHODS, ResolutionMethod
from .candidates import CandidateSource, ClaimCandidate

#: Strongest first. A cluster reports the best evidence any member carries,
#: because one direct parse is enough to know the number was really stated.
_METHOD_RANK: dict[ResolutionMethod, int] = {
    ResolutionMethod.AUTHOR_DECLARED: 0,
    ResolutionMethod.DIRECT_PARSE: 1,
    ResolutionMethod.NUMERIC_RECONCILIATION: 2,
    ResolutionMethod.AST_RESOLVED: 3,
    ResolutionMethod.ALIAS_RESOLVED: 4,
    ResolutionMethod.WRAPPER_RESOLVED: 5,
    ResolutionMethod.GRAPH_MATCH: 6,
    ResolutionMethod.LEXICAL_MATCH: 7,
    ResolutionMethod.MODEL_RANKED: 8,
}


def _decimals(value: float) -> int:
    """How many decimal places this value is stated to.

    ``repr`` already gives the shortest representation that round-trips, so a
    fraction it prints never carries a redundant trailing zero: ``repr(92.40)``
    is ``'92.4'``. The one string it does produce ending in a zero is ``'X.0'``,
    and that zero is the whole of the precision, not padding -- stripping it
    read ``54.0`` as the integer 54 and let it agree with everything within half
    a unit.
    """
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 12
    _, _, fraction = text.partition(".")
    return len(fraction)


def _agree(left: float, right: float) -> bool:
    """Do two values state the same number at the coarser one's precision?"""
    places = min(_decimals(left), _decimals(right))
    return round(left, places) == round(right, places)


@dataclass
class ClaimCluster:
    """One claim, and every place the artifact states it."""

    metric: str
    value: float
    members: list[ClaimCandidate] = field(default_factory=list)

    @property
    def method(self) -> ResolutionMethod:
        return min((m.method for m in self.members), key=lambda m: _METHOD_RANK[m])

    @property
    def confidence(self) -> float:
        return max(m.confidence for m in self.members)

    @property
    def sources(self) -> tuple[CandidateSource, ...]:
        return tuple(sorted({m.source for m in self.members}, key=lambda s: s.value))

    @property
    def restated(self) -> bool:
        """Stated in more than one place — the strongest form of a claim."""
        return len({(m.location.path, m.location.line) for m in self.members}) > 1

    @property
    def logical_id(self) -> str:
        """Stable across content edits: no line number, no member ordering."""
        return f"claim:{self.metric}@{self.value:g}"


def _sort_key(candidate: ClaimCandidate) -> tuple:
    """Total order over candidates, so clustering never depends on input order.

    The labels are part of the key because they decide membership below, and a
    field that decides membership cannot be left to fall back on input order.
    """
    return (
        candidate.metric,
        candidate.value,
        candidate.source.value,
        candidate.location.path,
        candidate.location.line,
        _METHOD_RANK[candidate.method],
        candidate.text,
        candidate.row_label or "",
        candidate.column_label or "",
    )


def _same_place_other_number(cluster: ClaimCluster, candidate: ClaimCandidate, /) -> bool:
    """Would joining put two *different* numbers from one location in one claim?

    Restatement is the premise clustering rests on: the abstract, a results
    table and the README stating one number are one claim. Two cells of a single
    table are not that. They are two measurements the paper reports side by
    side, and however closely they round together they were never one statement.
    Merging them destroys the coarser number and leaves the survivor carrying
    the other cell's row -- ConvNeXt reports 81.3 for Swin-T and 81.33 for its
    own ablation in one table, which agree at one decimal place.

    Identical values at one location are still one claim here. Where those cells
    name what they measured, :func:`_other_measurement` is what separates them;
    this rule is what remains for a number the parse could not label.
    """
    return any(
        member.location.path == candidate.location.path
        and member.location.line == candidate.location.line
        and member.value != candidate.value
        for member in cluster.members
    )


def _measurement(candidate: ClaimCandidate, /) -> tuple[str, str] | None:
    """Which measurement a candidate states: the row and column naming it.

    ``None`` where the candidate names neither, which is every prose match: a
    sentence reports a number without saying which cell of which table it
    restates, so it is free to join whichever cell states that number.
    """
    if candidate.row_label is None and candidate.column_label is None:
        return None
    return (candidate.row_label or "", candidate.column_label or "")


def _other_measurement(cluster: ClaimCluster, candidate: ClaimCandidate, /) -> bool:
    """Would joining put two *different* measurements in one claim?

    A location is too coarse to answer this. Every cell of one ``tabular``
    records the line the environment opens on, so two cells of one table are
    indistinguishable by locator, and cells of two tables are not compared at
    all -- which is how bert lost its own ``(Ens.+TriviaQA)`` F1 of 92.2 to an
    ELMo baseline of 92.2 printed in another table, and how barlowtwins' Table 6
    yielded three claims from eight cells.

    A row and a column together name what was measured, so two cells naming
    different ones are different measurements however closely their values
    round, and merging them destroys the coarser number and leaves the survivor
    carrying the other cell's row. Two cells naming the *same* measurement are
    still one claim -- barlowtwins repeats its baseline row in every ablation
    table, and that is one number stated several times.

    The labels are compared as printed, so where two documents name one
    measurement differently the cells stay two claims, each keeping its own
    locator: ConvNeXt's paper heads a column ``mIoU`` and its README heads the
    same numbers ``mIoU (ms+flip)``. Erasing the qualifier to recover that merge
    is not available -- the README prints ``mIoU`` and ``mIoU (ms+flip)`` side
    by side, and those are two measurements.
    """
    measurement = _measurement(candidate)
    if measurement is None:
        return False
    for member in cluster.members:
        other = _measurement(member)
        if other is not None and other != measurement:
            return True
    return False


def cluster_candidates(candidates: list[ClaimCandidate], /) -> list[ClaimCluster]:
    """Group candidates that state the same claim.

    Deterministic: candidates are totally ordered first, so the same inputs in
    any order produce byte-identical clusters in the same sequence.
    """
    clusters: list[ClaimCluster] = []
    by_metric: dict[str, list[ClaimCluster]] = {}

    for candidate in sorted(candidates, key=_sort_key):
        bucket = by_metric.setdefault(candidate.metric, [])
        for existing in bucket:
            if (
                _agree(existing.value, candidate.value)
                and not _same_place_other_number(existing, candidate)
                and not _other_measurement(existing, candidate)
            ):
                existing.members.append(candidate)
                # The more precise statement is the better representative.
                if _decimals(candidate.value) > _decimals(existing.value):
                    existing.value = candidate.value
                break
        else:
            fresh = ClaimCluster(
                metric=candidate.metric, value=candidate.value, members=[candidate]
            )
            bucket.append(fresh)
            clusters.append(fresh)

    clusters.sort(key=lambda c: (c.metric, c.value))
    return clusters


def certain(clusters: list[ClaimCluster], /) -> list[ClaimCluster]:
    """Clusters read rather than inferred — the ones safe to state plainly."""
    return [c for c in clusters if c.method in CERTAIN_METHODS]
