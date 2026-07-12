"""CodeMeta software metadata (``codemeta.json``)."""

from __future__ import annotations

import json

from ..engine import CheckResult
from ..model import sanitized_remote_url


def render(result: CheckResult) -> str:
    ev = result.evidence
    name = ev.manifest.paper.title or ev.latex.title or result.repo.root.name
    remotes = [r for r in result.repo.git.remotes if r.startswith("http")]
    doc: dict = {
        "@context": "https://doi.org/10.5063/schema/codemeta-2.0",
        "@type": "SoftwareSourceCode",
        "name": name,
        "description": f"Research software artifact for {name}. Complete this description.",
        "programmingLanguage": "Python",
    }
    if remotes:
        doc["codeRepository"] = sanitized_remote_url(remotes[0])
    if ev.deps.python_version:
        doc["softwareRequirements"] = [f"Python {ev.deps.python_version}"]
    if result.repo.git.head_commit:
        doc["version"] = result.repo.git.head_commit[:12]
    if ev.docs.license_file:
        doc["license"] = "https://spdx.org/licenses/ (set the SPDX identifier matching LICENSE)"
    return json.dumps(doc, indent=2)
