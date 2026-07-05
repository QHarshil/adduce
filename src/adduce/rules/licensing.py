"""Access & Legal: can others legally reuse and reliably cite this work?"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Rule, Status


class LicenseRule(Rule):
    id = "R-LIC-001"
    category = Category.ACCESS_LEGAL
    title = "License file present"
    rationale = "Without a license, reuse is legally undefined no matter how open the repository looks."
    weight = 3

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.docs.license_file:
            return self.finding(Status.PASS, confidence=0.95, message=f"License file present: {ev.docs.license_file}.")
        return self.finding(
            Status.FAIL,
            confidence=0.95,
            message="No LICENSE file at the repository root.",
            remediation="Add a LICENSE file (MIT and Apache-2.0 are common for research code).",
        )


class CitationRule(Rule):
    id = "R-LIC-002"
    category = Category.ACCESS_LEGAL
    title = "Citation metadata provided"
    rationale = (
        "CITATION.cff is machine-readable citation metadata that GitHub and Zenodo pick up "
        "automatically; a BibTeX block in the README is the manual fallback."
    )
    weight = 2
    fix_command = "adduce fix --scaffold citation"

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.docs.citation_file:
            return self.finding(Status.PASS, confidence=0.95, message=f"Citation metadata present: {ev.docs.citation_file}.")
        if ev.docs.has_bibtex or ev.docs.has_section("citation"):
            return self.finding(
                Status.PARTIAL,
                confidence=0.8,
                message="A citation section or BibTeX block exists in the README, but there is no machine-readable CITATION.cff.",
                remediation="Add a CITATION.cff; `adduce fix --scaffold citation` drafts one.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.9,
            message="No CITATION.cff and no citation section in the README.",
            remediation="Add a CITATION.cff; `adduce fix --scaffold citation` drafts one.",
        )


class ThirdPartyLicensesRule(Rule):
    id = "R-LIC-003"
    category = Category.ACCESS_LEGAL
    title = "Third-party asset licenses stated"
    rationale = (
        "Datasets and pretrained models come with their own terms; venues ask explicitly "
        "whether asset licenses were respected and stated."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses_any({"torch", "tensorflow", "sklearn", "jax", "transformers", "lightning", "pandas"})

    def evaluate(self, ev: Evidence) -> Finding:
        uses_assets = bool(ev.remote.references) or bool(ev.manifest.datasets) or ev.data.dataset_urls
        if not uses_assets:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No third-party datasets or model downloads detected."
            )
        manifest_licensed = any(d.license for d in ev.manifest.datasets)
        if manifest_licensed:
            return self.finding(Status.PASS, confidence=0.85, message="Dataset licenses recorded in the manifest.")
        if ev.docs.mentions_asset_licensing:
            return self.finding(
                Status.PARTIAL,
                confidence=0.55,
                message="The README discusses dataset/model licensing, but nothing machine-readable records it.",
                remediation="Record each dataset's license in .adduce/manifest.yaml.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.65,
            message="Third-party datasets/models are used but their licenses are not stated.",
            remediation="State the license and terms of each dataset and pretrained model in the README and the manifest.",
        )
