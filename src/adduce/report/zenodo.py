""".zenodo.json deposit metadata, so the first Zenodo archive is correct."""

from __future__ import annotations

import json

from ..engine import CheckResult


def render(result: CheckResult) -> str:
    ev = result.evidence
    title = ev.manifest.paper.title or ev.latex.title or result.repo.root.name
    doc: dict = {
        "title": title,
        "description": f"Research artifact for {title}: code, configuration, and instructions "
        "to reproduce the reported results. Complete this description before depositing.",
        "upload_type": "software",
        "creators": [{"name": "TODO Lastname, Firstname", "affiliation": "TODO"}],
        "keywords": ["reproducibility", "research software"],
        "access_right": "open",
    }
    if ev.docs.license_file:
        doc["license"] = "mit"  # set to the SPDX id matching LICENSE
    return json.dumps(doc, indent=2)
