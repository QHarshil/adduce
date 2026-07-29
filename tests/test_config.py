"""Configuration trust-boundary and schema validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from adduce.cli import app
from adduce.config import Config, load_config
from adduce.engine import run_check
from adduce.profiles import load_profile


def test_valid_standalone_config_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "adduce.toml").write_text(
        'profile = "strict"\n'
        'ignore = ["R-LIC-001", "R-DOC-001"]\n'
        'exclude = ["third_party"]\n'
        "fail-under = 82.5\n",
        encoding="utf-8",
    )

    assert load_config(tmp_path) == Config(
        profile="strict",
        ignore=frozenset({"R-LIC-001", "R-DOC-001"}),
        exclude=("third_party",),
        fail_under=82.5,
        source="adduce.toml",
    )


def test_valid_pyproject_config_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.adduce]\n"
        'profile = "acm"\n'
        'ignore = ["R-LIC-001"]\n'
        'exclude = ["vendor"]\n'
        "fail_under = 0\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.profile == "acm"
    assert config.ignore == {"R-LIC-001"}
    assert config.exclude == ("vendor",)
    assert config.fail_under == 0
    assert config.source == "pyproject.toml [tool.adduce]"


def test_repository_config_cannot_select_external_profile(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    external = tmp_path / "external.toml"
    external.write_text(
        'name = "untrusted"\n[weights]\ndeterminism = 999\n',
        encoding="utf-8",
    )
    (repository / "adduce.toml").write_text(
        f"profile = {str(external)!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="may select only a bundled profile"):
        run_check(repository)


def test_explicit_profile_path_remains_available_to_trusted_caller(tmp_path: Path) -> None:
    profile_path = tmp_path / "custom.toml"
    profile_path.write_text(
        'name = "custom"\n[weights]\ndeterminism = 1\n',
        encoding="utf-8",
    )

    profile = load_profile(str(profile_path))

    assert profile.name == "custom"


@pytest.mark.parametrize("filename", ["adduce.toml", "pyproject.toml"])
def test_config_symlink_is_rejected_without_reading_target(
    tmp_path: Path,
    filename: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.toml"
    content = (
        '[tool.adduce]\nprofile = "strict"\n'
        if filename == "pyproject.toml"
        else 'profile = "strict"\n'
    )
    outside.write_text(content, encoding="utf-8")
    try:
        (repository / filename).symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are not available on this platform")

    with pytest.raises(ValueError, match="non-symlink regular file"):
        load_config(repository)


def test_non_regular_config_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "adduce.toml").mkdir()

    with pytest.raises(ValueError, match="non-symlink regular file"):
        load_config(tmp_path)


def test_oversized_config_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "adduce.toml").write_bytes(b"#" * (1024 * 1024 + 1))

    with pytest.raises(ValueError, match="1048576-byte size limit"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("profile = 3\n", "'profile' must be a string"),
        ('ignore = "R-LIC-001"\n', "'ignore' must be an array of strings"),
        ("ignore = [1]\n", "'ignore' must be an array of strings"),
        ('exclude = "vendor"\n', "'exclude' must be an array of strings"),
        ('fail-under = "90"\n', "'fail-under' must be a number"),
        ("fail-under = true\n", "'fail-under' must be a number"),
        ("fail-under = nan\n", "must be a finite number"),
        ("fail-under = inf\n", "must be a finite number"),
        ("fail-under = -1\n", "must be a finite number"),
        ("fail-under = 101\n", "must be a finite number"),
        (
            "fail-under = 80\nfail_under = 90\n",
            "use only one of 'fail-under' and 'fail_under'",
        ),
        ("profile = [\n", "contains malformed TOML"),
    ],
)
def test_invalid_standalone_config_is_rejected(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    (tmp_path / "adduce.toml").write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_non_table_pyproject_adduce_config_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool]\nadduce = "strict"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"\[tool\.adduce\].*table"):
        load_config(tmp_path)


def test_invalid_pyproject_adduce_field_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.adduce]\nignore = "R-LIC-001"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="'ignore' must be an array of strings"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "content",
    [
        "fail-under = nan\n",
        'ignore = "R-LIC-001"\n',
        "profile = [\n",
    ],
)
def test_check_reports_invalid_config_as_usage_error(
    tmp_path: Path,
    content: str,
) -> None:
    (tmp_path / "model.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "adduce.toml").write_text(content, encoding="utf-8")

    result = CliRunner().invoke(app, ["check", str(tmp_path)])

    assert result.exit_code == 2
    assert "error: invalid adduce.toml" in result.output
    assert "Traceback" not in result.output
