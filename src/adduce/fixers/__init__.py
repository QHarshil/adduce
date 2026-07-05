"""Scaffolders: generate the files the checks ask for.

All scaffolds are non-destructive. They write new files only; the README
scaffold appends missing sections rather than rewriting existing content.
Existing files are never overwritten unless ``force=True``.
"""

from __future__ import annotations

import datetime
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from ..engine import CheckResult

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
            return remote.replace("git@github.com:", "https://github.com/").removesuffix(".git")
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


def scaffold_seeds(result: CheckResult, force: bool = False) -> ScaffoldResult:
    target = result.repo.root / "seed_utils.py"
    if target.exists() and not force:
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("seed_utils.py.j2").render(
        torch=result.repo.frameworks.uses("torch"),
    )
    target.write_text(content, encoding="utf-8")
    return ScaffoldResult(target, "created")


def scaffold_citation(result: CheckResult, force: bool = False) -> ScaffoldResult:
    target = result.repo.root / "CITATION.cff"
    if target.exists() and not force:
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("CITATION.cff.j2").render(
        title=result.repo.root.name,
        authors=[],
        repository_url=_git_remote_url(result),
        date=datetime.date.today().isoformat(),
        version="1.0.0",
    )
    target.write_text(content, encoding="utf-8")
    return ScaffoldResult(target, "created")


def scaffold_docker(result: CheckResult, force: bool = False) -> ScaffoldResult:
    target = result.repo.root / "Dockerfile"
    if target.exists() and not force:
        return ScaffoldResult(target, "skipped (exists)")
    version = result.evidence.deps.python_version or "3.11"
    match = re.search(r"(\d+\.\d+)", version)
    python_version = match.group(1) if match else "3.11"
    content = _env.get_template("Dockerfile.j2").render(
        python_version=python_version,
        requirements_file=_requirements_file(result),
        entrypoint=_entrypoint(result),
    )
    target.write_text(content, encoding="utf-8")
    return ScaffoldResult(target, "created")


def scaffold_runner(result: CheckResult, force: bool = False) -> ScaffoldResult:
    target = result.repo.root / "reproduce.sh"
    if target.exists() and not force:
        return ScaffoldResult(target, "skipped (exists)")
    content = _env.get_template("reproduce.sh.j2").render(entrypoint=_entrypoint(result))
    target.write_text(content, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return ScaffoldResult(target, "created")


def scaffold_readme(result: CheckResult, force: bool = False) -> ScaffoldResult:
    """Create a README skeleton, or append only the sections that are missing."""
    docs = result.evidence.docs
    existing = result.repo.root / (docs.readme_path or "README.md")
    context = {
        "title": result.repo.root.name,
        "repository_url": _git_remote_url(result),
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
        with existing.open("a", encoding="utf-8") as handle:
            handle.write("\n" + content.lstrip("\n"))
        return ScaffoldResult(existing, "appended")
    existing.write_text(content.lstrip("\n"), encoding="utf-8")
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
