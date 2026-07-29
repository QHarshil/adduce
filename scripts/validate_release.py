#!/usr/bin/env python3
"""Fail closed unless a release tag and every public version surface agree."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

_STABLE_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


class ReleaseValidationError(ValueError):
    """Release metadata is incomplete or internally inconsistent."""


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"{context} must be a mapping")
    return value


def _source_version(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ReleaseValidationError(f"cannot read package version: {exc}") from exc
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(values) != 1:
        raise ReleaseValidationError(
            "package must define exactly one string __version__"
        )
    return values[0]


def _yaml(path: Path, context: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ReleaseValidationError(f"cannot read {context}: {exc}") from exc
    return _object(value, context)


def _current_readme_release(text: str) -> str:
    matches: list[str] = re.findall(
        r"^.*PyPI[^\n]*`([^`]+)`[^\n]*current release\.[ \t]*$",
        text,
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise ReleaseValidationError(
            "README must identify exactly one current PyPI release"
        )
    return matches[0]


def _changelog_release_date(text: str, version: str) -> str:
    match = re.search(
        rf"^## \[{re.escape(version)}\] - "
        r"([0-9]{4}-[0-9]{2}-[0-9]{2})[ \t]*$",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise ReleaseValidationError(
            f"CHANGELOG.md has no dated [{version}] release section"
        )
    try:
        date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ReleaseValidationError("CHANGELOG.md release date is invalid") from exc
    return match.group(1)


def validate_release(root: Path, tag: str) -> str:
    """Return the stable version after validating every release surface."""
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ReleaseValidationError("release tag must use the form vX.Y.Z")
    version = tag[1:]
    if not _STABLE_VERSION_RE.fullmatch(version):
        raise ReleaseValidationError("release tag must use stable SemVer vX.Y.Z")

    try:
        pyproject = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseValidationError(f"cannot read pyproject.toml: {exc}") from exc
    project_version = _object(
        pyproject.get("project"), "project metadata"
    ).get("version")
    source_version = _source_version(root / "src" / "adduce" / "__init__.py")
    citation = _yaml(root / "CITATION.cff", "CITATION.cff")
    citation_version = citation.get("version")
    action = _yaml(root / "action.yml", "action.yml")
    action_version = _object(
        _object(action.get("inputs"), "action inputs").get("version"),
        "action version input",
    ).get("default")
    try:
        readme = (root / "README.md").read_text(encoding="utf-8")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseValidationError(
            f"cannot read release documentation: {exc}"
        ) from exc
    readme_version = _current_readme_release(readme)
    changelog_date = _changelog_release_date(changelog, version)

    surfaces = {
        "pyproject.toml": project_version,
        "src/adduce/__init__.py": source_version,
        "CITATION.cff": citation_version,
        "action.yml": action_version,
        "README.md": readme_version,
    }
    mismatches = {
        name: value for name, value in surfaces.items() if value != version
    }
    if mismatches:
        rendered = ", ".join(
            f"{name}={value!r}" for name, value in sorted(mismatches.items())
        )
        raise ReleaseValidationError(
            f"release metadata does not match tag {tag}: {rendered}"
        )
    citation_date = citation.get("date-released")
    if isinstance(citation_date, date):
        citation_date = citation_date.isoformat()
    if citation_date != changelog_date:
        raise ReleaseValidationError(
            "CITATION.cff date-released must match the CHANGELOG.md release date"
        )
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Annotated release tag, vX.Y.Z")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        version = validate_release(args.root.resolve(), args.tag)
    except ReleaseValidationError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 2
    print(f"release metadata is internally consistent for v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
