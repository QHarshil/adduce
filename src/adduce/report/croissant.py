"""Croissant (MLCommons) dataset metadata skeletons, one per manifest dataset."""

from __future__ import annotations

import json

from ..engine import CheckResult

_CONTEXT = {
    "@vocab": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/",
    "sc": "https://schema.org/",
    "conformsTo": "dct:conformsTo",
    "dct": "http://purl.org/dc/terms/",
}


def render(result: CheckResult) -> str:
    """One JSON document containing a croissant skeleton per known dataset.

    Split into per-dataset files (``<id>.croissant.json``) by ``adduce export``.
    """
    ev = result.evidence
    documents: dict[str, dict] = {}
    datasets = ev.manifest.datasets
    if not datasets:
        # Fall back to detected programmatic loads so the export is never empty
        # for repositories that clearly consume data.
        from ..manifest_builder import _draft_datasets

        datasets = _draft_datasets(ev)
    for dataset in datasets:
        doc: dict = {
            "@context": _CONTEXT,
            "@type": "sc:Dataset",
            "conformsTo": "http://mlcommons.org/croissant/1.0",
            "name": dataset.id,
            "description": f"Dataset used by {result.repo.root.name}. Complete this description.",
        }
        if dataset.source:
            doc["url"] = dataset.source
        if dataset.license:
            doc["license"] = dataset.license
        if dataset.checksum:
            doc["distribution"] = [
                {
                    "@type": "cr:FileObject",
                    "@id": f"{dataset.id}-archive",
                    "name": f"{dataset.id}-archive",
                    "contentUrl": dataset.source or "",
                    "sha256": dataset.checksum.removeprefix("sha256:"),
                }
            ]
        documents[dataset.id] = doc
    return json.dumps(documents, indent=2)
