"""Stage 6, in the part that needs no search: does a result file state this?

A claim and a logged number are reconciled when the log has a column naming the
claim's metric and a row holding the claim's value. That is a comparison of two
parsed facts, so it is deterministic and offline, and it is the difference
between naming the file that actually carries a number and naming whichever
result file happened to be first.

Only the direction that can be settled here is settled here. Finding the script
that *wrote* the log is graph traversal, and ranking candidates when nothing
matches exactly is a later stage with its own resolution method.
"""

from __future__ import annotations

from ..naming import canonical_metric
from .cluster import ClaimCluster, _agree


def matching_results(cluster: ClaimCluster, result_files: list, /) -> list[str]:
    """Result files stating this claim's metric at this claim's value.

    Sorted by path, so a claim with two matching logs names them in a stable
    order rather than in collector order.
    """
    matches: list[str] = []
    for result in result_files:
        for column, values in result.metrics.items():
            if canonical_metric(column) != cluster.metric:
                continue
            if any(_agree(value, cluster.value) for value in values):
                matches.append(result.path)
                break
    return sorted(matches)
