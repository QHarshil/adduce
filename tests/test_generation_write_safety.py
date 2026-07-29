"""Path-containment regressions for fixed repository generation outputs."""

from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from adduce import cli as cli_module
from adduce.cli import app
from adduce.ledger import Ledger, build_provenance, write_ledger
from adduce.manifest import (
    Claim,
    Manifest,
    SmokeTarget,
    load_manifest,
    write_manifest,
    write_manifest_proposal,
)
from adduce.safe_write import SafeWriteError

runner = CliRunner()


def _repo(path: Path) -> Path:
    path.mkdir()
    (path / "train.py").write_text("print('ok')\n", encoding="utf-8")
    return path


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")


def _empty_ledger() -> Ledger:
    return Ledger(
        artifact_path="checklist.md",
        artifact_sha256="0" * 64,
        provenance=build_provenance("checklist", "neurips", "author", None),
    )


def test_citation_scaffold_refuses_dangling_symlink_destination(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside.cff"
    link = repo / "CITATION.cff"
    _symlink(link, outside)

    result = runner.invoke(app, ["fix", str(repo), "--scaffold", "citation"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link citation scaffold destination" in result.output
    assert link.is_symlink()
    assert not outside.exists()


def test_readme_scaffold_never_writes_through_existing_symlink(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-readme.md"
    outside.write_text("preserve me\n", encoding="utf-8")
    link = repo / "README.md"
    _symlink(link, outside)

    result = runner.invoke(app, ["fix", str(repo), "--scaffold", "readme"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link README scaffold destination" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"
    assert link.is_symlink()


def test_readme_scaffold_refuses_multiply_linked_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-readme.md"
    outside.write_text("# Existing\n", encoding="utf-8")
    try:
        os.link(outside, repo / "README.md")
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    result = runner.invoke(app, ["fix", str(repo), "--scaffold", "readme"])

    assert result.exit_code == 2
    assert "error: refusing multiply-linked README scaffold destination" in result.output
    assert outside.read_text(encoding="utf-8") == "# Existing\n"


def test_readme_scaffold_does_not_emit_repository_values_as_shell_commands(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo-$(touch marker)")

    result = runner.invoke(app, ["fix", str(repo), "--scaffold", "readme"])

    assert result.exit_code == 0, result.output
    rendered = (repo / "README.md").read_text(encoding="utf-8")
    assert "$(touch marker)" not in rendered
    assert 'git clone -- "[AUTHOR REVIEW REQUIRED: repository URL]"' in rendered
    assert 'cd -- "[AUTHOR REVIEW REQUIRED: checkout directory]"' in rendered


def test_docker_scaffold_json_quotes_repository_entrypoint(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "train.py").unlink()
    malicious_name = 'entry"point\nRUN touch marker\n.py'
    try:
        (repo / malicious_name).write_text(
            "if __name__ == '__main__':\n    pass\n",
            encoding="utf-8",
        )
    except OSError as exc:
        pytest.skip(f"platform does not support the adversarial filename: {exc}")

    result = runner.invoke(app, ["fix", str(repo), "--scaffold", "docker"])

    assert result.exit_code == 0, result.output
    rendered = (repo / "Dockerfile").read_text(encoding="utf-8")
    command = next(line.removeprefix("CMD ") for line in rendered.splitlines() if line.startswith("CMD "))
    assert json.loads(command) == ["python", malicious_name]
    assert "\nRUN touch marker\n" not in rendered


def test_manifest_command_refuses_symlink_instead_of_creating_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-manifest.yaml"
    link = directory / "manifest.yaml"
    _symlink(link, outside)

    result = runner.invoke(app, ["manifest", str(repo)])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link manifest.yaml" in result.output
    assert link.is_symlink()
    assert not outside.exists()


def test_manifest_loader_refuses_existing_symlinked_author_content(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-manifest.yaml"
    outside.write_text("schema: adduce/1\npaper:\n  title: private\n", encoding="utf-8")
    _symlink(directory / "manifest.yaml", outside)

    manifest = load_manifest(repo)

    assert manifest.error == "refusing symbolic-link manifest.yaml"
    assert manifest.paper.title is None


def test_manifest_loader_refuses_multiply_linked_author_content(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-manifest.yaml"
    outside.write_text("schema: adduce/1\npaper:\n  title: private\n", encoding="utf-8")
    try:
        os.link(outside, directory / "manifest.yaml")
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    manifest = load_manifest(repo)

    assert manifest.error == "refusing multiply-linked manifest.yaml"
    assert manifest.paper.title is None


def test_manifest_preflights_json_mirror_before_creating_yaml(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-manifest.json"
    _symlink(directory / "manifest.json", outside)

    with pytest.raises(SafeWriteError, match="symbolic-link manifest JSON mirror"):
        write_manifest(repo, Manifest())

    assert not (directory / "manifest.yaml").exists()
    assert not outside.exists()


def test_manifest_proposal_refuses_symlink_candidate(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-proposal.yaml"
    _symlink(directory / "manifest.proposed.yaml", outside)

    with pytest.raises(SafeWriteError, match="symbolic-link manifest proposal"):
        write_manifest_proposal(repo, Manifest())

    assert not outside.exists()
    assert not (directory / "manifest.proposed.json").exists()


def test_checklist_refuses_symlinked_adduce_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-adduce"
    outside.mkdir()
    _symlink(repo / ".adduce", outside, directory=True)

    result = runner.invoke(app, ["checklist", str(repo), "--profile", "neurips"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link .adduce directory" in result.output
    assert not (outside / "evidence-ledger.json").exists()


@pytest.mark.parametrize("command", ["checklist", "appendix"])
def test_generated_artifact_is_not_written_when_ledger_is_invalid(
    tmp_path: Path,
    command: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    ledger = directory / "evidence-ledger.json"
    ledger.write_text("not JSON\n", encoding="utf-8")
    artifact = repo / f"{command}.md"

    result = runner.invoke(app, [command, str(repo), "--output", str(artifact)])

    assert result.exit_code == 2
    assert "invalid evidence ledger" in result.output
    assert not artifact.exists()
    assert ledger.read_text(encoding="utf-8") == "not JSON\n"


def test_checklist_preflights_symlinked_output_before_updating_ledger(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-checklist.md"
    outside.write_text("preserve me\n", encoding="utf-8")
    output = repo / "checklist.md"
    _symlink(output, outside)

    result = runner.invoke(
        app,
        ["checklist", str(repo), "--output", str(output)],
    )

    assert result.exit_code == 2
    assert "refusing symbolic-link output artifact" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"
    assert not (repo / ".adduce" / "evidence-ledger.json").exists()


def test_ledger_writer_refuses_symlinked_adduce_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-adduce"
    outside.mkdir()
    _symlink(repo / ".adduce", outside, directory=True)

    with pytest.raises(SafeWriteError, match="symbolic-link .adduce directory"):
        write_ledger(repo, _empty_ledger())

    assert not (outside / "evidence-ledger.json").exists()


def test_ledger_writer_refuses_symlinked_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-ledger.json"
    outside.write_text('{"preserve": true}\n', encoding="utf-8")
    _symlink(directory / "evidence-ledger.json", outside)

    with pytest.raises(SafeWriteError, match="symbolic-link evidence ledger"):
        write_ledger(repo, _empty_ledger())

    assert outside.read_text(encoding="utf-8") == '{"preserve": true}\n'


@pytest.mark.parametrize("force", [False, True])
def test_package_refuses_symlinked_bundle_directory(
    tmp_path: Path,
    force: bool,
) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-package"
    outside.mkdir()
    _symlink(repo / "adduce-submission", outside, directory=True)
    arguments = ["package", str(repo)]
    if force:
        arguments.append("--force")

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "error: refusing symbolic-link adduce-submission directory" in result.output
    assert list(outside.iterdir()) == []


def test_package_force_refuses_symlinked_child_and_preserves_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    package = repo / "adduce-submission"
    package.mkdir()
    outside = tmp_path / "outside-checklist.md"
    outside.write_text("preserve me\n", encoding="utf-8")
    _symlink(package / "checklist.md", outside)

    result = runner.invoke(app, ["package", str(repo), "--force"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link submission bundle artifact" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"


def test_package_force_replaces_regular_child_and_preserves_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    package = repo / "adduce-submission"
    package.mkdir()
    target = package / "checklist.md"
    target.write_text("old content\n", encoding="utf-8")
    target.chmod(0o640)
    original_mode = stat.S_IMODE(target.stat().st_mode)

    result = runner.invoke(app, ["package", str(repo), "--force"])

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") != "old content\n"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode


def test_package_force_refuses_stale_optional_artifact(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    citation = repo / "CITATION.cff"
    citation.write_text(
        'cff-version: 1.2.0\ntitle: "reviewed"\nmessage: "cite"\n',
        encoding="utf-8",
    )
    first = runner.invoke(app, ["package", str(repo)])
    assert first.exit_code == 0, first.output
    packaged = repo / "adduce-submission" / "citation.cff"
    previous = packaged.read_text(encoding="utf-8")
    citation.unlink()

    refreshed = runner.invoke(app, ["package", str(repo), "--force"])

    assert refreshed.exit_code == 2
    assert "refusing stale or unknown entries" in refreshed.output
    assert packaged.read_text(encoding="utf-8") == previous


def test_package_force_refuses_unknown_bundle_entry(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    first = runner.invoke(app, ["package", str(repo)])
    assert first.exit_code == 0, first.output
    unknown = repo / "adduce-submission" / "notes.txt"
    unknown.write_text("author notes\n", encoding="utf-8")

    refreshed = runner.invoke(app, ["package", str(repo), "--force"])

    assert refreshed.exit_code == 2
    assert "refusing stale or unknown entries" in refreshed.output
    assert unknown.read_text(encoding="utf-8") == "author notes\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask semantics")
def test_package_force_creates_missing_children_with_normal_mode(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    package = repo / "adduce-submission"
    package.mkdir()
    previous_umask = os.umask(0o022)
    try:
        result = runner.invoke(app, ["package", str(repo), "--force"])
    finally:
        os.umask(previous_umask)

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE((package / "checklist.md").stat().st_mode) == 0o644


def test_package_refuses_multiply_linked_citation_source(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-citation.cff"
    outside.write_text("cff-version: 1.2.0\ntitle: private\n", encoding="utf-8")
    try:
        os.link(outside, repo / "CITATION.cff")
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    result = runner.invoke(app, ["package", str(repo)])

    assert result.exit_code == 2
    assert "error: refusing multiply-linked citation source" in result.output
    assert not (repo / "adduce-submission" / "citation.cff").exists()
    assert outside.read_text(encoding="utf-8") == (
        "cff-version: 1.2.0\ntitle: private\n"
    )


@pytest.mark.parametrize("force", [False, True])
def test_export_refuses_symlink_destination(
    tmp_path: Path,
    force: bool,
) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-codemeta.json"
    link = repo / "codemeta.json"
    _symlink(link, outside)
    arguments = ["export", "codemeta", str(repo)]
    if force:
        arguments.append("--force")

    result = runner.invoke(app, arguments)

    assert result.exit_code == 2
    assert "error: refusing symbolic-link export destination" in result.output
    assert link.is_symlink()
    assert not outside.exists()


def test_export_all_preflights_every_destination_before_writing(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    outside = tmp_path / "outside-zenodo.json"
    outside.write_text("preserve me\n", encoding="utf-8")
    _symlink(repo / ".zenodo.json", outside)

    result = runner.invoke(app, ["export", "all", str(repo), "--force"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link export destination" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"
    assert not (repo / "ro-crate-metadata.json").exists()
    assert not (repo / "codemeta.json").exists()


def test_baseline_refuses_symlink_destination(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    outside = tmp_path / "outside-baseline.json"
    outside.write_text("preserve me\n", encoding="utf-8")
    _symlink(directory / "baseline.json", outside)

    result = runner.invoke(app, ["baseline", str(repo)])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link baseline" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"


def test_pin_remotes_write_refuses_source_replaced_by_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    source = repo / "model.py"
    source.write_text(
        "from transformers import AutoModel\n"
        'AutoModel.from_pretrained("org/model")\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside-model.py"
    outside.write_text("preserve me\n", encoding="utf-8")
    probe = tmp_path / "symlink-probe"
    _symlink(probe, outside)
    probe.unlink()

    def replace_source_with_symlink(_result: object) -> list[tuple[str, str, str]]:
        source.unlink()
        source.symlink_to(outside)
        return [("hf-model", "org/model", "a" * 40)]

    monkeypatch.setattr(cli_module, "_resolve_and_print", replace_source_with_symlink)

    result = runner.invoke(app, ["pin-remotes", str(repo), "--write"])

    assert result.exit_code == 2
    assert "error: refusing symbolic-link remote-pinning source file" in result.output
    assert outside.read_text(encoding="utf-8") == "preserve me\n"


def test_pin_remotes_write_preserves_concurrent_regular_file_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    source = repo / "model.py"
    source.write_text(
        "from transformers import AutoModel\n"
        'AutoModel.from_pretrained("org/model")\n',
        encoding="utf-8",
    )

    def replace_source(_result: object) -> list[tuple[str, str, str]]:
        replacement = repo / "replacement.py"
        replacement.write_text("concurrent user edit\n", encoding="utf-8")
        os.replace(replacement, source)
        return [("hf-model", "org/model", "a" * 40)]

    monkeypatch.setattr(cli_module, "_resolve_and_print", replace_source)

    result = runner.invoke(app, ["pin-remotes", str(repo), "--write"])

    assert result.exit_code == 2
    assert "error: refusing changed remote-pinning source file" in result.output
    assert source.read_text(encoding="utf-8") == "concurrent user edit\n"


def test_pin_remotes_write_refuses_symlinked_ancestor_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    source_directory = repo / "src"
    source = source_directory / "pkg" / "model.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from transformers import AutoModel\n"
        'AutoModel.from_pretrained("org/model")\n',
        encoding="utf-8",
    )
    outside_directory = tmp_path / "outside-src"
    outside_source = outside_directory / "pkg" / "model.py"
    outside_source.parent.mkdir(parents=True)
    outside_source.write_text("preserve me\n", encoding="utf-8")
    probe = tmp_path / "symlink-probe"
    _symlink(probe, outside_directory, directory=True)
    probe.unlink()

    def replace_ancestor_with_symlink(_result: object) -> list[tuple[str, str, str]]:
        source_directory.rename(repo / "original-src")
        source_directory.symlink_to(outside_directory, target_is_directory=True)
        return [("hf-model", "org/model", "a" * 40)]

    monkeypatch.setattr(cli_module, "_resolve_and_print", replace_ancestor_with_symlink)

    result = runner.invoke(app, ["pin-remotes", str(repo), "--write"])

    assert result.exit_code == 2
    assert (
        "error: refusing symbolic-link ancestor of remote-pinning source directory"
        in result.output
    )
    assert outside_source.read_text(encoding="utf-8") == "preserve me\n"


def test_pin_remotes_write_replaces_regular_source_and_preserves_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path / "repo")
    source = repo / "model.py"
    source.write_text(
        "from transformers import AutoModel\n"
        'AutoModel.from_pretrained("org/model")\n',
        encoding="utf-8",
    )
    source.chmod(0o640)
    original_mode = stat.S_IMODE(source.stat().st_mode)
    monkeypatch.setattr(
        cli_module,
        "_resolve_and_print",
        lambda _result: [("hf-model", "org/model", "a" * 40)],
    )

    result = runner.invoke(app, ["pin-remotes", str(repo), "--write"])

    assert result.exit_code == 0, result.output
    updated = source.read_text(encoding="utf-8")
    assert "revision" in updated
    assert "a" * 40 in updated
    assert stat.S_IMODE(source.stat().st_mode) == original_mode


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            {"schema": "adduce/1", "claims": [{"id": "C1", "value": math.nan}]},
            "claims[0].value must be a finite number",
        ),
        (
            {"schema": "adduce/1", "claims": [{"id": "C1", "seeds": [math.inf]}]},
            "every claims[0].seeds entry must be an integer",
        ),
        (
            {"schema": "adduce/1", "smoke": {"max_runtime_minutes": math.nan}},
            "smoke.max_runtime_minutes must be an integer from 1 to 1440",
        ),
    ],
)
def test_manifest_rejects_non_finite_untrusted_numbers(
    tmp_path: Path,
    document: dict,
    message: str,
) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(document),
        encoding="utf-8",
    )

    manifest = load_manifest(repo)

    assert manifest.error == f"invalid manifest: {message}"


@pytest.mark.parametrize(
    "manifest",
    [
        Manifest(claims=[Claim(id="C1", value=math.nan)]),
        Manifest(claims=[Claim(id="C1", seeds=[math.inf])]),  # type: ignore[list-item]
        Manifest(smoke=SmokeTarget(max_runtime_minutes=math.inf)),  # type: ignore[arg-type]
    ],
)
def test_manifest_serialization_rejects_non_finite_values_before_writing(
    tmp_path: Path,
    manifest: Manifest,
) -> None:
    repo = _repo(tmp_path / "repo")

    with pytest.raises(SafeWriteError, match="cannot be serialized safely"):
        write_manifest(repo, manifest)

    assert not (repo / ".adduce").exists()


def test_invalid_existing_ledger_is_preserved(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    directory = repo / ".adduce"
    directory.mkdir()
    target = directory / "evidence-ledger.json"
    target.write_text("not JSON\n", encoding="utf-8")

    with pytest.raises(SafeWriteError, match="invalid evidence ledger"):
        write_ledger(repo, _empty_ledger())

    assert target.read_text(encoding="utf-8") == "not JSON\n"
