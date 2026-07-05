"""Structured JSON output for dashboards and programmatic consumers."""

from __future__ import annotations

import json

from .. import __version__
from ..engine import CheckResult


def render(result: CheckResult) -> str:
    reviewer_time = result.reviewer_time
    payload = {
        "tool": {"name": "adduce", "version": __version__},
        "repository": {
            "root": str(result.repo.root),
            "commit": result.repo.git.head_commit,
            "frameworks": sorted(result.repo.frameworks.detected),
            "files_scanned": len(result.repo.files),
        },
        "reviewer_time": {
            "low_minutes": reviewer_time.low_minutes,
            "high_minutes": reviewer_time.high_minutes,
            "bucket": reviewer_time.bucket,
            "unknown": reviewer_time.unknown,
            "factors": reviewer_time.factors,
        },
        "claims": [
            {
                "id": trail.claim.id,
                "headline": trail.headline,
                "status": trail.status.value,
                "inferred": trail.inferred,
                "trail": [
                    {
                        "label": entry.label,
                        "value": entry.value,
                        "note": entry.note,
                        "resolved": entry.resolved,
                    }
                    for entry in trail.entries
                ],
            }
            for trail in result.graph.trails
        ],
        **result.card.to_dict(),
    }
    return json.dumps(payload, indent=2)
