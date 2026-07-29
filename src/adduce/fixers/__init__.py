"""Scaffolders: generate the files the checks ask for.

All scaffolds are non-destructive. They write new files only; the README
scaffold appends missing sections rather than rewriting existing content.
Existing files are never overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from ..engine import CheckResult
from ..model import sanitized_remote_url
from ..safe_write import append_text_regular, create_text_exclusive, regular_file_exists

_env = Environment(
    loader=PackageLoader("adduce.fixers", "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


@dataclass
class ScaffoldResult:
    path: Path
    action: str  # "created", "appended", "skipped (exists)"


def _git_remote_url(result: CheckResult) -> str | None:
    for remote in result.repo.git.remotes:
        if remote.startswith(("https://", "git@")):
            cleaned = sanitized_remote_url(remote)
            return cleaned.replace("git@github.com:", "https://github.com/").removesuffix(".git")
    return None


def _requirements_file(result: CheckResult) -> str | None:
    for name in ("requirements.txt", "requirements/requirements.txt"):
        if result.repo.exists(name):
            return name
    return None


def _entrypoint(result: CheckResult) -> str:
    if result.evidence.env.entrypoint_files:
        return sorted(result.evidence.env.entrypoint_files)[0]
    guards = result.evidence.py.main_guard_files
    return guards[0] if guards else "main.py"


def scaffold_seeds(result: CheckResult) -> ScaffoldResult:
    target = result.repo.root / "seed_utils.py"
    if regular_file_exists(target, label="seed scaffold destination"):
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("seed_utils.py.j2").render(
        torch=result.repo.frameworks.uses("torch"),
    )
    create_text_exclusive(target, content, label="seed scaffold destination")
    return ScaffoldResult(target, "created")


def scaffold_citation(result: CheckResult) -> ScaffoldResult:
    target = result.repo.root / "CITATION.cff"
    if regular_file_exists(target, label="citation scaffold destination"):
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("CITATION.cff.j2").render(
        title=result.repo.root.name,
        authors=[],
        repository_url=_git_remote_url(result),
    )
    create_text_exclusive(target, content, label="citation scaffold destination")
    return ScaffoldResult(target, "created")


def scaffold_docker(result: CheckResult) -> ScaffoldResult:
    target = result.repo.root / "Dockerfile"
    if regular_file_exists(target, label="Dockerfile scaffold destination"):
        return ScaffoldResult(target, "skipped (exists)")
    version = result.evidence.deps.python_version or "3.11"
    match = re.search(r"(\d+\.\d+)", version)
    python_version = match.group(1) if match else "3.11"
    content = _env.get_template("Dockerfile.j2").render(
        python_version=python_version,
        requirements_file=_requirements_file(result),
        entrypoint=_entrypoint(result),
    )
    create_text_exclusive(target, content, label="Dockerfile scaffold destination")
    return ScaffoldResult(target, "created")


def scaffold_runner(result: CheckResult) -> ScaffoldResult:
    target = result.repo.root / "reproduce.sh"
    if regular_file_exists(target, label="runner scaffold destination"):
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("reproduce.sh.j2").render()
    create_text_exclusive(
        target,
        content,
        label="runner scaffold destination",
        mode=0o777,
    )
    return ScaffoldResult(target, "created")


def scaffold_readme(result: CheckResult) -> ScaffoldResult:
    """Create a README skeleton, or append only the sections that are missing."""
    docs = result.evidence.docs
    existing = result.repo.root / (docs.readme_path or "README.md")
    context = {
        "commit": (result.repo.git.head_commit or "")[:7] or None,
        "include_title": not docs.has_readme,
        "include_install": not docs.has_section("install"),
        "include_usage": not docs.has_section("usage"),
        "include_data": not docs.has_section("data"),
        "include_results": not docs.has_section("results"),
        "include_hardware": not docs.has_section("hardware"),
    }
    if docs.has_readme and not any(
        context[key] for key in ("include_install", "include_usage", "include_data", "include_results", "include_hardware")
    ):
        return ScaffoldResult(existing, "skipped (all sections present)")
    content = _env.get_template("readme_sections.md.j2").render(**context)
    if docs.has_readme:
        append_text_regular(
            existing,
            "\n" + content.lstrip("\n"),
            label="README scaffold destination",
        )
        return ScaffoldResult(existing, "appended")
    if regular_file_exists(existing, label="README scaffold destination"):
        return ScaffoldResult(existing, "skipped (exists)")
    create_text_exclusive(
        existing,
        content.lstrip("\n"),
        label="README scaffold destination",
    )
    return ScaffoldResult(existing, "created")


SCAFFOLDS = {
    "seeds": (scaffold_seeds, "seed_utils.py with comprehensive, layered seeding"),
    "citation": (scaffold_citation, "CITATION.cff citation metadata"),
    "docker": (scaffold_docker, "Dockerfile capturing the runtime environment"),
    "runner": (scaffold_runner, "reproduce.sh one-command reproduction skeleton"),
    "readme": (scaffold_readme, "README skeleton or missing reproducibility sections"),
}

#: Rules that map directly onto a scaffold, for ``adduce fix --rule``.
RULE_TO_SCAFFOLD = {
    "R-DET-001": "seeds",
    "R-DET-002": "seeds",
    "R-DET-003": "seeds",
    "R-DET-004": "seeds",
    "R-DET-005": "seeds",
    "R-LIC-002": "citation",
    "R-ENV-003": "docker",
    "R-EXEC-002": "runner",
    "R-DOC-001": "readme",
    "R-DOC-003": "readme",
    "R-PREC-001": "readme",
    "R-PREC-002": "readme",
    "R-PREC-005": "readme",
}
