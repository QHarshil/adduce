"""SARIF 2.1.0 output for GitHub code scanning and other SARIF consumers.

Only actionable findings (fail/partial) become results; passes are encoded
implicitly by absence, which is how code-scanning consumers expect it.
"""

from __future__ import annotations

import hashlib
import json

from .. import __version__
from ..engine import CheckResult
from ..rules.base import Finding, Status

_LEVELS = {Status.FAIL: "warning", Status.PARTIAL: "note"}
_DOCS_BASE = "https://github.com/QHarshil/adduce/blob/main/docs/rules"


def _fingerprint(finding: Finding, path: str) -> str:
    return hashlib.sha256(f"{finding.rule_id}:{path}".encode()).hexdigest()[:16]


def render(result: CheckResult) -> str:
    rules_seen: dict[str, dict] = {}
    results: list[dict] = []

    for finding in result.card.findings:
        level = _LEVELS.get(finding.status)
        if level is None or finding.suppressed:
            continue
        if finding.rule_id not in rules_seen:
            rules_seen[finding.rule_id] = {
                "id": finding.rule_id,
                "name": finding.title.replace(" ", ""),
                "shortDescription": {"text": finding.title},
                "fullDescription": {"text": finding.remediation or finding.title},
                "helpUri": f"{_DOCS_BASE}/{finding.rule_id}.md",
                "defaultConfiguration": {"level": "warning"},
            }
        locations = finding.locations[:5]
        primary_path = locations[0].path if locations else "README.md"
        if locations:
            sarif_locations = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": loc.path, "uriBaseId": "%SRCROOT%"},
                        **({"region": {"startLine": loc.line}} if loc.line else {}),
                    }
                }
                for loc in locations
            ]
        else:
            # Repository-level findings anchor to the repo root's README.
            sarif_locations = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": primary_path, "uriBaseId": "%SRCROOT%"},
                    }
                }
            ]
        results.append(
            {
                "ruleId": finding.rule_id,
                "level": level,
                "message": {"text": f"{finding.message} {finding.remediation}".strip()},
                "locations": sarif_locations,
                "partialFingerprints": {
                    "adduceFindingKey": _fingerprint(finding, primary_path),
                },
            }
        )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "adduce",
                        "informationUri": "https://github.com/QHarshil/adduce",
                        "version": __version__,
                        "rules": list(rules_seen.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
