"""Release metadata and Trusted Publishing workflow gates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts.validate_release import ReleaseValidationError, validate_release

ROOT = Path(__file__).resolve().parents[1]


def _release_fixture(tmp_path: Path, version: str = "1.2.3") -> Path:
    (tmp_path / "src" / "adduce").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "adduce"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "adduce" / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (tmp_path / "CITATION.cff").write_text(
        f'cff-version: 1.2.0\nversion: "{version}"\n'
        'date-released: "2026-07-26"\n',
        encoding="utf-8",
    )
    (tmp_path / "action.yml").write_text(
        f'inputs:\n  version:\n    default: "{version}"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        f"[PyPI `{version}`](https://pypi.org/project/adduce/{version}/) "
        "is the current release.\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-07-26\n",
        encoding="utf-8",
    )
    return tmp_path


def test_release_metadata_gate_accepts_consistent_stable_version(tmp_path):
    root = _release_fixture(tmp_path)

    assert validate_release(root, "v1.2.3") == "1.2.3"


def test_release_metadata_gate_accepts_yaml_date_value(tmp_path):
    root = _release_fixture(tmp_path)
    citation = root / "CITATION.cff"
    citation.write_text(
        citation.read_text(encoding="utf-8").replace(
            'date-released: "2026-07-26"',
            "date-released: 2026-07-26",
        ),
        encoding="utf-8",
    )

    assert validate_release(root, "v1.2.3") == "1.2.3"


@pytest.mark.parametrize(
    ("path", "old", "new", "message"),
    [
        ("pyproject.toml", 'version = "1.2.3"', 'version = "1.2.4"', "pyproject.toml"),
        ("CITATION.cff", 'version: "1.2.3"', 'version: "1.2.4"', "CITATION.cff"),
        ("action.yml", 'default: "1.2.3"', 'default: "1.2.4"', "action.yml"),
        ("README.md", "`1.2.3`", "`1.2.4`", "README.md"),
    ],
)
def test_release_metadata_gate_rejects_version_drift(
    tmp_path,
    path,
    old,
    new,
    message,
):
    root = _release_fixture(tmp_path)
    target = root / path
    target.write_text(
        target.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseValidationError, match=message):
        validate_release(root, "v1.2.3")


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2", "v1.2.3.dev0", "v01.2.3"])
def test_release_metadata_gate_rejects_non_stable_tags(tmp_path, tag):
    root = _release_fixture(tmp_path)

    with pytest.raises(ReleaseValidationError, match="release tag|stable SemVer"):
        validate_release(root, tag)


def test_trusted_publishing_workflow_has_narrow_permissions_and_triggers():
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(text, Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"push"}
    assert workflow["on"]["push"]["tags"] == ["v*"]
    assert workflow["permissions"] == {"contents": "read"}
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "build"
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}
    assert len(publish["steps"]) == 2
    assert publish["steps"][0]["uses"].endswith(
        "@d3f86a106a0bac45b974a628896c90dbdf5c8093"
    )
    assert publish["steps"][1]["uses"].endswith(
        "@ba38be9e461d3875417946c167d0b5f3d385a247"
    )
    assert "workflow_dispatch" not in text
    assert "pull_request_target" not in text
    assert "password:" not in text
    assert "secrets." not in text


def _sdist_include() -> list[str]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]

    with (ROOT / "pyproject.toml").open("rb") as handle:
        metadata = tomllib.load(handle)
    targets = metadata["tool"]["hatch"]["build"]["targets"]
    include = targets["sdist"]["include"]
    assert isinstance(include, list)
    return [str(entry) for entry in include]


def _covered_by_sdist(relative: str, include: list[str]) -> bool:
    """Whether an sdist include entry selects this repository-relative path."""
    for entry in include:
        pattern = entry.rstrip("/")
        if relative == pattern or relative.startswith(f"{pattern}/"):
            return True
    return False


def test_sdist_ships_every_documentation_page_the_readme_links_to() -> None:
    """The source distribution carries the docs tree, not a subset of it.

    The README is a landing page that links outward, so a sdist missing
    ``docs/`` would ship a tarball whose documentation had gone nowhere.
    """
    include = _sdist_include()

    pages = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs").rglob("*.md")
    )
    assert pages, "expected documentation pages under docs/"

    uncovered = [page for page in pages if not _covered_by_sdist(page, include)]
    assert uncovered == [], (
        "sdist include does not cover these documentation pages: "
        f"{uncovered[:5]} ({len(uncovered)} total)"
    )


def test_readme_documentation_links_resolve_to_files_in_the_tree() -> None:
    """Every docs page the README points at exists and ships.

    README is the PyPI long description, so its links are published as
    immutable metadata; a dangling target cannot be corrected after release.
    """
    import re

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = sorted(
        set(
            re.findall(
                r"https://github\.com/QHarshil/adduce/(?:blob|tree)/main/"
                r"(docs/[^)#]+)",
                text,
            )
        )
    )
    assert targets, "expected the README to link into docs/"

    include = _sdist_include()
    for target in targets:
        assert (ROOT / target).exists(), f"README links to missing {target}"
        assert _covered_by_sdist(target, include), (
            f"README links to {target}, which the sdist does not ship"
        )


def _action_score_command() -> str:
    """The score expression the shipped composite Action actually runs.

    Extracted from ``action.yml`` rather than restated, so the test cannot
    drift away from the file it is guarding.
    """
    text = (ROOT / "action.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        prefix = 'SCORE=$(python -c "'
        if stripped.startswith(prefix):
            rest = stripped[len(prefix) :]
            return rest[: rest.index('"')]
    raise AssertionError("action.yml no longer contains a SCORE=$(python -c ...) line")


@pytest.mark.parametrize(
    ("total", "expected"),
    [(47.1, "47.1"), (0.0, "0.0"), (None, "")],
)
def test_the_action_reports_an_absent_score_as_empty_never_as_none(
    tmp_path: Path, total: object, expected: str
) -> None:
    """A card that assessed nothing has a null total, and ``print`` would emit
    the string ``None`` for it. A workflow comparing that against a threshold
    reads an absence as a value, so the Action must emit nothing instead.

    ``0.0`` is included deliberately: an all-FAIL card scores zero and must
    still be reported as a number.
    """
    import json
    import subprocess
    import sys

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"total": total}), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-c", _action_score_command(), str(report)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == expected
    assert completed.stdout.strip() != "None"
