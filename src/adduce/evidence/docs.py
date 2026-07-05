"""README and documentation evidence: is the setup actually written down?"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Repo

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_FENCE_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)
# Commands that *run* something. Installation commands (pip/conda install)
# deliberately do not count: they document setup, not reproduction.
_COMMAND_RE = re.compile(
    r"^\s*\$?\s*(python3?\s+(?!-m\s+pip\b)\S+|make\s+\w|bash\s+\S+|sh\s+\S+|\./\S+\.sh|docker\s+run|uv\s+run|torchrun|accelerate\s+launch)",
    re.MULTILINE,
)
_COMMIT_REF_RE = re.compile(r"(commit|checkout|revision|rev)\s+[`\"']?[0-9a-f]{7,40}\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)

_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "install": ("install", "installation", "setup", "getting started", "environment", "requirements", "dependencies"),
    "usage": ("usage", "how to run", "running", "quickstart", "quick start", "training", "train", "reproduc", "experiments", "evaluation", "demo", "example"),
    "results": ("results", "expected results", "expected output", "benchmark", "performance", "leaderboard", "main results"),
    "hardware": ("hardware", "compute", "gpu", "resources", "runtime", "system requirements"),
    "data": ("data", "dataset", "datasets", "download"),
    "citation": ("citation", "citing", "cite", "bibtex", "reference"),
    "license": ("license", "licence"),
}


@dataclass
class DocsEvidence:
    readme_path: str | None = None
    headings: list[str] = field(default_factory=list)
    sections: set[str] = field(default_factory=set)
    run_commands: list[str] = field(default_factory=list)
    mentions_seed: bool = False
    mentions_hardware_inline: bool = False
    references_commit: bool = False
    dois: list[str] = field(default_factory=list)
    has_results_table: bool = False
    license_file: str | None = None
    citation_file: str | None = None
    has_bibtex: bool = False
    mentions_asset_licensing: bool = False

    @property
    def has_readme(self) -> bool:
        return self.readme_path is not None

    def has_section(self, key: str) -> bool:
        return key in self.sections


def _classify_headings(headings: list[str]) -> set[str]:
    found: set[str] = set()
    for heading in headings:
        lowered = heading.lower()
        for key, keywords in _SECTION_KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                found.add(key)
    return found


_HARDWARE_INLINE_RE = re.compile(
    r"\b(nvidia|a100|v100|h100|rtx\s?\d{4}|t4\b|tpu|cuda\s+\d|\d+\s?gb\s+(v?ram|gpu|memory)|gpu[- ]hours?|single\s+gpu|\d+\s+gpus?)\b",
    re.IGNORECASE,
)
_RESULTS_TABLE_RE = re.compile(r"^\|.+\|\s*$\n^\|[-:| ]+\|\s*$", re.MULTILINE)


def collect_docs(repo: Repo) -> DocsEvidence:
    evidence = DocsEvidence()

    readmes = repo.find_names("README.md", "README.rst", "README.txt", "README")
    root_readmes = [f for f in readmes if len(f.path.parts) == 1]
    if root_readmes:
        evidence.readme_path = str(root_readmes[0].path)

    licenses = repo.find_names("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE")
    root_licenses = [f for f in licenses if len(f.path.parts) == 1]
    if root_licenses:
        evidence.license_file = str(root_licenses[0].path)

    citations = repo.find_names("CITATION.cff", "citation.cff")
    if citations:
        evidence.citation_file = str(citations[0].path)

    if evidence.readme_path:
        content = repo.read_text(evidence.readme_path) or ""
        matches = _HEADING_RE.findall(content)
        evidence.headings = [text for _, text in matches]
        # The first level-1 heading is the project title, not a section:
        # a repo named "demo" or "training-tricks" must not satisfy the
        # usage-section check by name alone.
        section_headings = [
            text
            for index, (hashes, text) in enumerate(matches)
            if not (index == 0 and len(hashes) == 1)
        ]
        evidence.sections = _classify_headings(section_headings)
        for fence in _FENCE_RE.findall(content):
            evidence.run_commands.extend(
                m.group(0).strip() for m in _COMMAND_RE.finditer(fence)
            )
        evidence.mentions_seed = bool(re.search(r"\bseed", content, re.IGNORECASE))
        evidence.mentions_hardware_inline = bool(_HARDWARE_INLINE_RE.search(content))
        evidence.references_commit = bool(_COMMIT_REF_RE.search(content))
        evidence.dois = list(dict.fromkeys(_DOI_RE.findall(content)))
        evidence.has_results_table = bool(_RESULTS_TABLE_RE.search(content))
        lowered = content.lower()
        evidence.has_bibtex = "@inproceedings" in lowered or "@article" in lowered or "@misc" in lowered
        evidence.mentions_asset_licensing = "licen" in lowered and ("dataset" in lowered or "model" in lowered)

    return evidence
