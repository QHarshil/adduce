"""Version-control and archival evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Repo
from .docs import DocsEvidence


@dataclass
class GitEvidence:
    is_repo: bool = False
    has_tags: bool = False
    head_commit: str | None = None
    commit_referenced_in_readme: bool = False
    has_zenodo_config: bool = False
    has_archival_doi: bool = False


def collect_git(repo: Repo, docs: DocsEvidence) -> GitEvidence:
    evidence = GitEvidence(
        is_repo=repo.git.is_repo,
        has_tags=bool(repo.git.tags),
        head_commit=repo.git.head_commit,
        commit_referenced_in_readme=docs.references_commit,
        has_zenodo_config=repo.exists(".zenodo.json"),
    )
    evidence.has_archival_doi = any(
        "zenodo" in doi.lower() or doi.startswith("10.5281/") for doi in docs.dois
    ) or evidence.has_zenodo_config
    return evidence
