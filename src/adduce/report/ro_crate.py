"""RO-Crate 1.1 metadata (``ro-crate-metadata.json``): the research object
packaged in the standard the digital-preservation world consumes."""

from __future__ import annotations

import json

from ..engine import CheckResult


def render(result: CheckResult) -> str:
    ev = result.evidence
    name = ev.manifest.paper.title or ev.latex.title or result.repo.root.name

    has_part: list[dict] = []
    graph: list[dict] = []

    def add_file(path: str, entity_type: str = "File", extra: dict | None = None) -> None:
        has_part.append({"@id": path})
        entity = {"@id": path, "@type": entity_type}
        if extra:
            entity.update(extra)
        graph.append(entity)

    if ev.docs.readme_path:
        add_file(ev.docs.readme_path, extra={"description": "Project documentation"})
    if ev.docs.license_file:
        add_file(ev.docs.license_file, extra={"description": "License"})
    if ev.docs.citation_file:
        add_file(ev.docs.citation_file, extra={"description": "Citation metadata"})
    for manifest_file in ev.deps.declaration_files[:3]:
        add_file(manifest_file, extra={"description": "Dependency manifest"})
    for dockerfile in ev.env.dockerfiles[:1]:
        add_file(dockerfile, extra={"description": "Container definition"})
    for dataset in ev.manifest.datasets:
        entity: dict = {"@id": f"#dataset-{dataset.id}", "@type": "Dataset", "name": dataset.id}
        if dataset.source:
            entity["url"] = dataset.source
        if dataset.license:
            entity["license"] = dataset.license
        graph.append(entity)
        has_part.append({"@id": f"#dataset-{dataset.id}"})

    root: dict = {
        "@id": "./",
        "@type": "Dataset",
        "name": name,
        "description": f"Research artifact for {name}.",
        "hasPart": has_part,
    }
    if ev.docs.license_file:
        root["license"] = {"@id": ev.docs.license_file}
    if result.repo.git.head_commit:
        root["version"] = result.repo.git.head_commit[:12]

    crate = {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {
                "@id": "ro-crate-metadata.json",
                "@type": "CreativeWork",
                "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
                "about": {"@id": "./"},
            },
            root,
            *graph,
        ],
    }
    return json.dumps(crate, indent=2)
