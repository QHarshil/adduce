"""Archival readiness: will this artifact still resolve in ten years?"""

from __future__ import annotations

from ..evidence import Evidence
from .base import Category, Finding, Location, Rule, Status


class ArchivalIdentifierRule(Rule):
    id = "R-ARC-001"
    category = Category.ARCHIVAL
    title = "Archival identifier (DOI / SWHID)"
    rationale = (
        "GitHub repositories move and disappear; an archival deposit with a persistent "
        "identifier is what 'Artifacts Available' badging requires."
    )
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.git.has_archival_doi:
            return self.finding(
                Status.PASS, confidence=0.8, message="Archival deposit detected (Zenodo DOI or .zenodo.json configuration)."
            )
        if ev.docs.dois:
            return self.finding(
                Status.PARTIAL,
                confidence=0.6,
                message="DOIs are referenced in the README, but none clearly archives this repository itself.",
                remediation="Archive a tagged release on Zenodo (GitHub integration) and put the concept DOI in the README; `adduce archive-plan` lists the steps.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="No archival identifier detected for this repository.",
            remediation="Run `adduce archive-plan` for the exact steps to obtain a Zenodo DOI or Software Heritage SWHID.",
        )


class ArchivableAsIsRule(Rule):
    id = "R-ARC-002"
    category = Category.ARCHIVAL
    title = "Repository archivable as-is"
    rationale = "Multi-gigabyte committed binaries blow past archive size limits and block a clean deposit."
    weight = 1

    _ARCHIVE_BUDGET = 2 * 1024 * 1024 * 1024  # a conservative deposit budget

    def evaluate(self, ev: Evidence) -> Finding:
        total = sum(f.size for f in ev.repo.files)
        offenders = [b for b in ev.data.untracked_binaries if b.size > 200 * 1024 * 1024]
        if total <= self._ARCHIVE_BUDGET and not offenders:
            return self.finding(
                Status.PASS,
                confidence=0.8,
                message=f"Repository size ({total / (1024 * 1024):.0f} MiB) is comfortably archivable.",
            )
        detail = f"total size {total / (1024 * 1024 * 1024):.1f} GiB" if total > self._ARCHIVE_BUDGET else ""
        if offenders:
            detail += ("; " if detail else "") + f"{len(offenders)} file(s) over 200 MiB"
        return self.finding(
            Status.PARTIAL,
            confidence=0.75,
            message=f"Archiving would be blocked or bloated: {detail}.",
            remediation="Move large artifacts to a data/model host and archive the code plus pointers, not the blobs.",
            locations=[Location(b.path) for b in offenders[:5]],
        )


class ArchivalMetadataRule(Rule):
    id = "R-ARC-003"
    category = Category.ARCHIVAL
    title = "Machine-readable archival metadata (.zenodo.json / codemeta.json)"
    rationale = (
        "Without deposit metadata, the Zenodo record inherits whatever GitHub guesses; "
        ".zenodo.json and codemeta.json make the archival record correct on first deposit."
    )
    weight = 1

    def evaluate(self, ev: Evidence) -> Finding:
        has_zenodo = ev.repo.exists(".zenodo.json")
        has_codemeta = ev.repo.exists("codemeta.json")
        if has_zenodo and has_codemeta:
            return self.finding(Status.PASS, confidence=0.9, message=".zenodo.json and codemeta.json present.")
        if has_zenodo or has_codemeta:
            missing = "codemeta.json" if has_zenodo else ".zenodo.json"
            return self.finding(
                Status.PARTIAL,
                confidence=0.85,
                message=f"Partial archival metadata; missing {missing}.",
                remediation=f"Generate it with `adduce export {'codemeta' if has_zenodo else 'zenodo'}`.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.85,
            message="No archival metadata files (.zenodo.json, codemeta.json).",
            remediation="Generate both with `adduce export zenodo` and `adduce export codemeta`.",
        )
