"""SARIF 2.1.0 output for GitHub code scanning and other SARIF consumers.

Only actionable findings (fail/partial) become results; passes are encoded
implicitly by absence, which is how code-scanning consumers expect it.
"""

from __future__ import annotations

import hashlib
import json

from .. import __version__
from ..engine import CheckResult
from ..model import Repo
from ..rules.base import Finding, Status

_LEVELS = {Status.FAIL: "warning", Status.PARTIAL: "note"}
_DOCS_BASE = "https://github.com/QHarshil/adduce/blob/main/docs/rules"


def _fingerprint(finding: Finding, path: str) -> str:
    return hashlib.sha256(f"{finding.rule_id}:{path}".encode()).hexdigest()[:16]


def _repo_anchor(repo: Repo) -> str | None:
    """The path a repository-level finding anchors to, or None for no anchor.

    Only inventoried paths are eligible, so the anchor never names a file that
    is not in the audited repository; a finding that carries its own location
    does not pass through here. The choice reads the inventory and never the
    filesystem, and is deterministic regardless of inventory order:
    a repo-root README.md, else the first repo-root file whose name stems from
    README in any spelling, else the shallowest path with ties broken
    lexicographically. None when nothing was inventoried at all.
    """
    paths = sorted(str(f.path) for f in repo.files)
    if not paths:
        return None
    root = [p for p in paths if "/" not in p]
    if "README.md" in root:
        return "README.md"
    readme = next((p for p in root if p.split(".", 1)[0].upper() == "README"), None)
    return readme or min(paths, key=lambda p: (p.count("/"), p))


def _region(line: int | None) -> dict[str, dict[str, int]]:
    """The region for a location that reports a line, and none for one that does not.

    A SARIF region is where a result was detected, so a file-scoped location
    stays file-level rather than claiming a detection on some arbitrary line.
    Every collector numbers lines from 1, which makes a non-positive line a
    collector bug: it fails here rather than reaching a consumer as SARIF that
    violates the schema's startLine minimum at exit 0.
    """
    if line is None:
        return {}
    if line < 1:
        raise ValueError(f"location line is not 1-based, got {line!r}")
    return {"region": {"startLine": line}}


def render(result: CheckResult) -> str:
    rules_seen: dict[str, dict] = {}
    results: list[dict] = []

    for finding in result.card.findings:
        level = _LEVELS.get(finding.status)
        if level is None:
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
        anchor = None if locations else _repo_anchor(result.repo)
        primary_path = locations[0].path if locations else (anchor or "")
        if locations:
            sarif_locations: list[dict] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": loc.path, "uriBaseId": "%SRCROOT%"},
                        **_region(loc.line),
                    }
                }
                for loc in locations
            ]
        elif anchor is not None:
            # A repository-level finding has neither a file nor a line of its
            # own, so the whole anchor is a navigation target rather than
            # evidence: an inventoried file at line 1, which gives a consumer
            # that keys result identity on the region a stable value.
            sarif_locations = [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": anchor, "uriBaseId": "%SRCROOT%"},
                        "region": {"startLine": 1},
                    }
                }
            ]
        else:
            # Nothing was inventoried, so there is no file to point at. SARIF
            # permits a result with no locations, and naming an absent path
            # would report a file that was never observed.
            sarif_locations = []
        sarif_result = {
                "ruleId": finding.rule_id,
                "level": level,
                "message": {"text": f"{finding.message} {finding.remediation}".strip()},
                "locations": sarif_locations,
                "partialFingerprints": {
                    "adduceFindingKey": _fingerprint(finding, primary_path),
                },
            }
        if finding.items:
            # SARIF is machine-readable, so every child is carried, uncapped and
            # in the same shape the JSON report uses. The property bag is the
            # standard extension point; it is absent, not empty, when a finding
            # has no children, which is how a SARIF consumer already reads it.
            sarif_result["properties"] = {
                "adduceFindingItems": [item.to_dict() for item in finding.items]
            }
        if finding.suppressed:
            sarif_result["suppressions"] = [
                {
                    "kind": "external",
                    "status": "accepted",
                    "justification": (
                        "Suppressed by Adduce policy; the observed finding and score are retained."
                    ),
                }
            ]
        results.append(sarif_result)

    sarif = {
        "$schema": "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json",
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
                "properties": {
                    "adduceConfiguration": {
                        "source": result.config.source or "",
                        "repositoryPolicyHonored": (
                            result.config.repository_policy_honored
                        ),
                        "profile": result.config.profile,
                        "ignoredRules": sorted(result.config.ignore),
                        "excludedPaths": list(result.config.exclude),
                    }
                },
                "results": results,
            }
        ],
    }
    # allow_nan=False: a NaN/Infinity confidence must fail loudly here rather
    # than reach a consumer as invalid JSON at exit 0.
    return json.dumps(sarif, allow_nan=False, indent=2)
