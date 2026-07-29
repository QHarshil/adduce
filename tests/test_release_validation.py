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
