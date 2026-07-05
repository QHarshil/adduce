"""Dependency declarations and how tightly they are pinned."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum

from ..model import Repo

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib


class PinLevel(Enum):
    EXACT = "exact"          # foo==1.2.3, a lockfile entry, or a pinned VCS ref
    BOUNDED = "bounded"      # foo~=1.2 or foo>=1.2,<2.0
    UNBOUNDED = "unbounded"  # foo, foo>=1.2, foo>1


@dataclass(frozen=True)
class Dependency:
    name: str
    specifier: str
    pin: PinLevel
    source: str  # file it was declared in


_LOCKFILES = (
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "pdm.lock",
    "conda-lock.yml",
    "conda-lock.yaml",
    "requirements.lock",
)

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_SHA_RE = re.compile(r"@[0-9a-f]{7,40}\b")


def _classify_specifier(spec: str) -> PinLevel:
    spec = spec.split(";")[0].split("#")[0].strip()
    if not spec:
        return PinLevel.UNBOUNDED
    if "==" in spec or "===" in spec:
        return PinLevel.EXACT
    if "~=" in spec:
        return PinLevel.BOUNDED
    has_lower = ">=" in spec or ">" in spec
    has_upper = "<" in spec
    if has_lower and has_upper:
        return PinLevel.BOUNDED
    return PinLevel.UNBOUNDED


def _parse_requirement_line(line: str, source: str) -> Dependency | None:
    line = line.strip()
    if not line or line.startswith(("#", "-r", "--", "-c", "-e ")):
        return None
    if line.startswith(("git+", "hg+", "svn+", "http://", "https://")):
        pin = PinLevel.EXACT if _SHA_RE.search(line) else PinLevel.UNBOUNDED
        return Dependency(name=line, specifier=line, pin=pin, source=source)
    match = _NAME_RE.match(line)
    if not match:
        return None
    name, _extras, spec = match.groups()
    return Dependency(name=name.lower(), specifier=spec.strip(), pin=_classify_specifier(spec), source=source)


@dataclass
class DependencyEvidence:
    dependencies: list[Dependency] = field(default_factory=list)
    declaration_files: list[str] = field(default_factory=list)
    lockfiles: list[str] = field(default_factory=list)
    python_version: str | None = None
    python_version_source: str | None = None

    @property
    def declared(self) -> bool:
        return bool(self.declaration_files)

    @property
    def has_lockfile(self) -> bool:
        return bool(self.lockfiles)

    def count(self, pin: PinLevel) -> int:
        return sum(1 for d in self.dependencies if d.pin is pin)

    @property
    def pinned_fraction(self) -> float:
        """Fraction of dependencies resolvable to an exact version.

        A lockfile pins everything it covers, so its presence counts as full
        pinning regardless of how loose the direct declarations are.
        """
        if self.has_lockfile:
            return 1.0
        if not self.dependencies:
            return 0.0
        exact = self.count(PinLevel.EXACT)
        bounded = self.count(PinLevel.BOUNDED)
        return (exact + 0.5 * bounded) / len(self.dependencies)


def _parse_requirements_txt(repo: Repo, path: str, evidence: DependencyEvidence) -> None:
    content = repo.read_text(path)
    if content is None:
        return
    deps = [d for line in content.splitlines() if (d := _parse_requirement_line(line, path))]
    if deps:
        evidence.dependencies.extend(deps)
    evidence.declaration_files.append(path)


def _parse_pyproject(repo: Repo, evidence: DependencyEvidence) -> None:
    content = repo.read_text("pyproject.toml")
    if content is None:
        return
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return
    project = data.get("project", {})
    declared = False
    for dep in project.get("dependencies", []) or []:
        if isinstance(dep, str) and (parsed := _parse_requirement_line(dep, "pyproject.toml")):
            evidence.dependencies.append(parsed)
            declared = True
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if name.lower() == "python":
            continue
        spec_str = spec if isinstance(spec, str) else str(spec.get("version", "")) if isinstance(spec, dict) else ""
        pin = PinLevel.EXACT if re.fullmatch(r"=?=?\d[\w.]*", spec_str or "") else (
            PinLevel.BOUNDED if spec_str.startswith(("^", "~")) else _classify_specifier(spec_str)
        )
        evidence.dependencies.append(
            Dependency(name=name.lower(), specifier=spec_str, pin=pin, source="pyproject.toml")
        )
        declared = True
    if declared:
        evidence.declaration_files.append("pyproject.toml")
    requires_python = project.get("requires-python") or (
        poetry.get("dependencies", {}) or {}
    ).get("python")
    if requires_python and evidence.python_version is None:
        evidence.python_version = str(requires_python)
        evidence.python_version_source = "pyproject.toml"


def _parse_conda_env(repo: Repo, path: str, evidence: DependencyEvidence) -> None:
    content = repo.read_text(path)
    if content is None:
        return
    evidence.declaration_files.append(path)
    in_deps = False
    for raw in content.splitlines():
        line = raw.rstrip()
        if re.match(r"^dependencies\s*:", line):
            in_deps = True
            continue
        if in_deps:
            if line and not line.startswith((" ", "-", "\t")):
                in_deps = False
                continue
            item = line.strip()
            if not item.startswith("- ") or item.startswith("- pip:"):
                continue
            entry = item[2:].strip().strip("'\"")
            if not entry or ":" in entry:
                continue
            name, sep, version = entry.partition("=")
            name = name.strip().lower()
            if name == "python":
                if evidence.python_version is None and version:
                    evidence.python_version = version.strip("=")
                    evidence.python_version_source = path
                continue
            pin = PinLevel.EXACT if sep and re.match(r"=?\d", version) else PinLevel.UNBOUNDED
            evidence.dependencies.append(
                Dependency(name=name, specifier=entry, pin=pin, source=path)
            )


def collect_dependencies(repo: Repo) -> DependencyEvidence:
    evidence = DependencyEvidence()

    for entry in repo.files:
        rel = str(entry.path)
        name = entry.name.lower()
        if entry.name in _LOCKFILES:
            evidence.lockfiles.append(rel)
        elif re.fullmatch(r"requirements[\w.-]*\.txt", name) and len(entry.path.parts) <= 2:
            if "dev" not in name and "test" not in name and "docs" not in name and "lint" not in name:
                _parse_requirements_txt(repo, rel, evidence)
        elif name in {"environment.yml", "environment.yaml"} and len(entry.path.parts) == 1:
            _parse_conda_env(repo, rel, evidence)

    if repo.exists("pyproject.toml"):
        _parse_pyproject(repo, evidence)
    for legacy in ("setup.py", "setup.cfg", "Pipfile"):
        if repo.exists(legacy) and legacy not in evidence.declaration_files:
            evidence.declaration_files.append(legacy)

    if evidence.python_version is None:
        for candidate in (".python-version", "runtime.txt"):
            content = repo.read_text(candidate) if repo.exists(candidate) else None
            if content and content.strip():
                evidence.python_version = content.strip().splitlines()[0]
                evidence.python_version_source = candidate
                break
    if evidence.python_version is None:
        dockerfiles = repo.find_names("Dockerfile") + repo.find("Dockerfile.*")
        for docker in dockerfiles:
            content = repo.read_text(docker.path) or ""
            match = re.search(r"^FROM\s+.*python[:\-](\d+\.\d+[\w.]*)", content, re.MULTILINE | re.IGNORECASE)
            if match:
                evidence.python_version = match.group(1)
                evidence.python_version_source = str(docker.path)
                break

    return evidence
