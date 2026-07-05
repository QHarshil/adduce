"""Portability: things that only work on the author's machine."""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status


class AbsolutePathRule(Rule):
    id = "R-PORT-001"
    category = Category.PORTABILITY
    title = "No local absolute paths"
    rationale = "Paths under /Users, /home/<name>, or C:\\Users fail on every machine but one."
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        hits = ev.portability.of_kind("abs_path")
        if not hits:
            return self.finding(Status.PASS, confidence=0.85, message="No local absolute paths detected.")
        examples = "; ".join(dict.fromkeys(h.detail for h in hits[:3]))
        return self.finding(
            Status.PARTIAL if len(hits) <= 2 else Status.FAIL,
            confidence=0.8,
            message=f"{len(hits)} local absolute path reference(s) found (e.g. {examples}).",
            remediation="Replace with repository-relative paths or a configurable data root.",
            locations=[Location(h.file, h.line) for h in hits[:5]],
        )


class LocalhostRule(Rule):
    id = "R-PORT-002"
    category = Category.PORTABILITY
    title = "No hardcoded localhost endpoints"
    rationale = "A hardcoded localhost:port assumes a service the reviewer's machine is not running."
    weight = 1

    def applies_to(self, repo: Repo) -> bool:
        return bool(repo.python_files())

    def evaluate(self, ev: Evidence) -> Finding:
        hits = ev.portability.of_kind("localhost")
        if not hits:
            return self.finding(Status.PASS, confidence=0.8, message="No hardcoded localhost endpoints detected.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,  # dev servers and dashboards are often intentional
            message=f"{len(hits)} hardcoded localhost endpoint(s) found.",
            remediation="Make hosts/ports configurable, or document the service that must be running.",
            locations=[Location(h.file, h.line) for h in hits[:5]],
        )


class PrivateDataSourceRule(Rule):
    id = "R-PORT-003"
    category = Category.PORTABILITY
    title = "No private buckets or drive links as data sources"
    rationale = (
        "Google Drive links and s3:///gs:// buckets rot, throttle, and are frequently "
        "permissioned; they are the least durable data path an artifact can have."
    )
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        gdrive = ev.remote.by_kind("gdrive")
        buckets = ev.remote.by_kind("bucket")
        if not gdrive and not buckets:
            return self.finding(Status.PASS, confidence=0.8, message="No drive links or private buckets used as data sources.")
        parts = []
        if gdrive:
            parts.append(f"{len(gdrive)} Google Drive reference(s)")
        if buckets:
            parts.append(f"{len(buckets)} S3/GCS URI(s)")
        hits = [*gdrive, *buckets]
        return self.finding(
            Status.PARTIAL,
            confidence=0.75,
            message=", ".join(parts) + " serve as data sources.",
            remediation="Mirror the data to an archival host (Zenodo, Hugging Face datasets) with a DOI and checksums.",
            locations=[Location(h.file, h.line) for h in hits[:5]],
        )


class SecretsRule(Rule):
    id = "R-PORT-004"
    category = Category.PORTABILITY
    title = "No hardcoded secrets or API keys"
    rationale = "A committed key is a security incident and blocks publishing the artifact at all."
    weight = 3
    severity = "high"  # a security incident regardless of its score weight

    def evaluate(self, ev: Evidence) -> Finding:
        hits = ev.portability.of_kind("secret")
        if not hits:
            return self.finding(Status.PASS, confidence=0.8, message="No recognisable secrets detected.")
        kinds = ", ".join(dict.fromkeys(h.detail for h in hits))
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message=f"{len(hits)} potential secret(s) detected ({kinds}). Values are not echoed.",
            remediation=(
                "Revoke the credentials, purge them from git history (git filter-repo), and load "
                "secrets from the environment instead."
            ),
            locations=[Location(h.file, h.line) for h in hits[:5]],
        )
