""".zenodo.json deposit metadata, so the first Zenodo archive is correct."""

from __future__ import annotations

import json

from ..engine import CheckResult


def render(result: CheckResult) -> str:
    ev = result.evidence
    title = ev.manifest.paper.title or ev.latex.title or result.repo.root.name
    doc: dict = {
        "title": title,
        "description": f"Research artifact for {title}. [AUTHOR REVIEW REQUIRED: describe the "
        "artifact contents and the claims it supports before depositing.]",
        "upload_type": "software",
        "creators": [{"name": "[AUTHOR REVIEW REQUIRED: Lastname, Firstname]"}],
        "keywords": ["reproducibility", "research software"],
    }
    return json.dumps(doc, indent=2)
