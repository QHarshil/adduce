"""The expanded command surface: manifest, focused audits, exports, badge,
diff, archive-plan, pin-remotes (offline half), and the codemod."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml
from typer.testing import CliRunner

from adduce import cli as cli_module
from adduce.cli import app
from adduce.fixers.codemods.pin_revision import pin_revisions
from tests.conftest import plain
from tests.test_engine import BARE, WELL_FORMED, _write

# Wide columns keep phrases on one line; plain() strips color codes.
runner = CliRunner(env={"COLUMNS": "300"})


def test_manifest_command_scaffolds(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["manifest", str(tmp_path)])
    assert result.exit_code == 0
    manifest_path = tmp_path / ".adduce" / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert data["schema"] == "adduce/1"
    assert (tmp_path / ".adduce" / "manifest.json").is_file()


def test_manifest_force_alias_writes_non_destructive_proposal(tmp_path):
    _write(tmp_path, WELL_FORMED)
    first = runner.invoke(app, ["manifest", str(tmp_path)])
    assert first.exit_code == 0, first.output
    target = tmp_path / ".adduce" / "manifest.yaml"
    original = target.read_text(encoding="utf-8")

    refreshed = runner.invoke(app, ["manifest", str(tmp_path), "--force"])

    assert refreshed.exit_code == 0, refreshed.output
    assert target.read_text(encoding="utf-8") == original
    assert (tmp_path / ".adduce" / "manifest.proposed.yaml").is_file()


def test_manifest_preserves_author_content(tmp_path):
    _write(tmp_path, WELL_FORMED)
    (tmp_path / ".adduce").mkdir()
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump({"schema": "adduce/1", "paper": {"title": "My Real Title"}}), encoding="utf-8"
    )
    runner.invoke(app, ["manifest", str(tmp_path)])
    data = yaml.safe_load((tmp_path / ".adduce" / "manifest.yaml").read_text(encoding="utf-8"))
    assert data["paper"]["title"] == "My Real Title"


def test_drift_command_without_paper(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["drift", str(tmp_path)])
    assert result.exit_code == 0
    assert "nothing to compare" in plain(result.output)


def test_precision_and_deps_commands(tmp_path):
    files = dict(BARE)
    files["train.py"] = "import torch\nimport cv2\ntorch.backends.cuda.matmul.allow_tf32 = True\n"
    _write(tmp_path, files)
    precision = runner.invoke(app, ["precision", str(tmp_path)])
    assert precision.exit_code == 0
    assert "allow_tf32" in plain(precision.output)
    deps = runner.invoke(app, ["deps", str(tmp_path)])
    assert deps.exit_code == 0
    assert "R-DEP-010" in plain(deps.output)


def test_export_all(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["export", "all", str(tmp_path)])
    assert result.exit_code == 0
    for filename in ("ro-crate-metadata.json", "codemeta.json", ".zenodo.json", "checksums.txt", "SOFTWARE_HERITAGE.md"):
        assert (tmp_path / filename).is_file(), filename
    crate = json.loads((tmp_path / "ro-crate-metadata.json").read_text(encoding="utf-8"))
    assert crate["@context"].startswith("https://w3id.org/ro/crate")
    # Idempotent: second run skips.
    rerun = runner.invoke(app, ["export", "all", str(tmp_path)])
    assert "skipped (exists)" in plain(rerun.output)


def test_export_unknown_errors(tmp_path):
    _write(tmp_path, BARE)
    assert runner.invoke(app, ["export", "nope", str(tmp_path)]).exit_code == 2


def test_badge_svg(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["badge", str(tmp_path), "--svg"])
    assert result.exit_code == 0
    assert result.output.startswith("<svg")
    assert "reproducibility" in result.output


def test_appendix_command(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["appendix", str(tmp_path)])
    assert result.exit_code == 0
    assert "Artifact Appendix" in result.output
    assert "A.2 Artifact check-list" in result.output


def test_archive_plan(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["archive-plan", str(tmp_path)])
    assert result.exit_code == 0
    assert "Zenodo" in result.output
    assert "in your browser" in plain(result.output)


def _git(tmp_path, *args):
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def test_diff_flags_undocumented_changes(tmp_path):
    _write(tmp_path, WELL_FORMED)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "train.py").write_text("import torch\nprint('changed')\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "change code only")
    result = runner.invoke(app, ["diff", "HEAD~1..HEAD", str(tmp_path)])
    assert result.exit_code == 1
    assert "may now be stale" in plain(result.output)


def test_diff_accepts_documented_changes(tmp_path):
    _write(tmp_path, WELL_FORMED)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "train.py").write_text("import torch\nprint('changed')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# updated\n\n## Usage\n\nnew numbers documented\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "change code and docs")
    result = runner.invoke(app, ["diff", "HEAD~1..HEAD", str(tmp_path)])
    assert result.exit_code == 0


@pytest.mark.skipif(sys.platform == "win32", reason="executable Git helper fixtures use POSIX shebangs")
def test_diff_never_executes_repository_or_environment_helpers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    files = dict(WELL_FORMED)
    files[".gitattributes"] = "*.py diff=untrusted\n"
    _write(repo, files)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    markers = {
        name: tmp_path / f"{name}.executed"
        for name in ("fsmonitor", "external", "textconv", "environment")
    }

    def helper(name):
        target = tmp_path / f"{name}-helper.py"
        target.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            f"Path({str(markers[name])!r}).touch()\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
        return target

    helpers = {name: helper(name) for name in markers}
    _git(repo, "config", "core.fsmonitor", str(helpers["fsmonitor"]))
    _git(repo, "config", "diff.external", str(helpers["external"]))
    _git(repo, "config", "diff.untrusted.textconv", str(helpers["textconv"]))
    (repo / "train.py").write_text("print('changed')\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["diff", "HEAD", str(repo)],
        env={
            "GIT_EXTERNAL_DIFF": str(helpers["environment"]),
            "GIT_DIR": str(tmp_path / "untrusted-git-dir"),
            "GIT_WORK_TREE": str(tmp_path / "untrusted-work-tree"),
        },
    )

    assert result.exit_code == 1, result.output
    assert all(not marker.exists() for marker in markers.values())


def test_diff_rejects_option_like_revision_range(tmp_path):
    result = runner.invoke(app, ["diff", "--", "--no-index", str(tmp_path)])

    assert result.exit_code == 2
    assert "invalid Git revision range" in plain(result.output)


def test_pin_remotes_offline_listing(tmp_path):
    files = dict(BARE)
    files["model.py"] = "from transformers import AutoModel\nAutoModel.from_pretrained('bert-base-uncased')\n"
    _write(tmp_path, files)
    result = runner.invoke(app, ["pin-remotes", str(tmp_path)])
    assert result.exit_code == 0
    assert "without an immutable revision" in plain(result.output)
    assert "opt-in online step" in plain(result.output)  # never resolves without --diff/--write


def test_check_online_updates_machine_readable_remote_finding(tmp_path, monkeypatch):
    from adduce.dynamic.resolve import Resolution

    files = dict(BARE)
    files["download.sh"] = "curl https://example.org/artifact.bin\n"
    _write(tmp_path, files)
    monkeypatch.setattr(
        "adduce.dynamic.resolve.resolve_references",
        lambda references, cache: [
            Resolution(
                "https://example.org/artifact.bin?token=secret",
                "url",
                None,
                True,
                "HTTP 200, 10 bytes, etag [bold]literal[/bold]\x1b[2J",
            )
        ],
    )

    result = runner.invoke(
        app,
        ["check", str(tmp_path), "--online", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    finding = next(item for item in payload["findings"] if item["rule_id"] == "R-REMOTE-005")
    assert finding["status"] == "pass"
    assert "all 1 supported" in finding["message"]
    assert "Online resolution" in result.stderr
    assert "/<redacted-path>?<redacted>" in result.stderr
    assert "token=secret" not in result.stderr
    assert "[bold]literal[/bold]" in result.stderr
    assert "\x1b" not in result.stderr


def test_check_online_does_not_pass_when_reference_kind_is_unsupported(
    tmp_path, monkeypatch
):
    from adduce.dynamic.resolve import Resolution

    files = dict(BARE)
    files["download.sh"] = (
        "curl https://example.org/artifact.bin\n"
        "aws s3 cp s3://research-bucket/artifact.bin .\n"
    )
    _write(tmp_path, files)
    monkeypatch.setattr(
        "adduce.dynamic.resolve.resolve_references",
        lambda references, cache: [
            Resolution(
                "https://example.org/artifact.bin",
                "url",
                None,
                True,
                "HTTP 200, 10 bytes",
            ),
            Resolution(
                "s3://research-bucket/artifact.bin",
                "bucket",
                None,
                False,
                "no supported public-metadata resolver",
                supported=False,
            ),
        ],
    )

    result = runner.invoke(
        app,
        ["check", str(tmp_path), "--online", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    finding = next(item for item in payload["findings"] if item["rule_id"] == "R-REMOTE-005")
    assert finding["status"] == "partial"
    assert "no supported resolver" in finding["message"]


def test_check_only_and_skip_filters(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path), "--only", "R-DET", "--format", "json"])
    payload = json.loads(result.output)
    assert all(f["rule_id"].startswith("R-DET") for f in payload["findings"])
    result = runner.invoke(app, ["check", str(tmp_path), "--skip", "R-DET", "--format", "json"])
    payload = json.loads(result.output)
    assert not any(f["rule_id"].startswith("R-DET") for f in payload["findings"])


def test_check_modes_render(tmp_path):
    _write(tmp_path, WELL_FORMED)
    reviewer = runner.invoke(app, ["check", str(tmp_path), "--mode", "reviewer"])
    assert reviewer.exit_code == 0
    assert "Could not be verified" in plain(reviewer.output)
    chair = runner.invoke(app, ["check", str(tmp_path), "--mode", "ae-chair"])
    assert chair.exit_code == 0
    assert "Badge prerequisites" in plain(chair.output)
    assert "never an award prediction" in plain(chair.output)


def test_json_includes_reviewer_time_and_claims(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "json"])
    payload = json.loads(result.output)
    assert "reviewer_time" in payload and payload["reviewer_time"]["bucket"]
    assert "claims" in payload


def test_reproduce_requires_confirmation(tmp_path):
    _write(tmp_path, WELL_FORMED)
    command = "python -c 'print(1)' --token literal-secret"
    result = runner.invoke(
        app,
        [
            "reproduce",
            str(tmp_path),
            "--command",
            command,
            "--expected-metric",
            "accuracy",
        ],
    )
    assert result.exit_code == 2
    assert "--yes" in plain(result.output)
    assert "provides input isolation only" in plain(result.output)
    assert "literal-secret" not in result.output


def test_reproduce_smoke_run_agrees(tmp_path):
    _write(tmp_path, WELL_FORMED)
    command = (
        f"{sys.executable} -c \"import json; json.dump({{'accuracy': 0.5}}, "
        "open('out.json','w')); print('accuracy: 0.5')\""
    )
    result = runner.invoke(
        app,
        [
            "reproduce",
            str(tmp_path),
            "--command",
            command,
            "--expected-metric",
            "accuracy",
            "--yes",
            "--timeout-minutes",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "runs agree" in plain(result.output)
    assert "provides input isolation only" in plain(result.output)
    report_path = tmp_path / ".adduce" / "reproduce-report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["comparable_fingerprints"] == ["metric:accuracy"]


def test_reproduce_rejects_invalid_timeout_before_execution(tmp_path):
    _write(tmp_path, WELL_FORMED)
    marker = tmp_path / "executed"
    command = f"{sys.executable} -c \"from pathlib import Path; Path('executed').touch()\""

    result = runner.invoke(
        app,
        [
            "reproduce",
            str(tmp_path),
            "--command",
            command,
            "--expected-metric",
            "accuracy",
            "--timeout-minutes",
            "0",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "between 1 and 1440" in plain(result.output)
    assert not marker.exists()
    assert not (tmp_path / ".adduce" / "reproduce-report.json").exists()


def test_reproduce_uses_manifest_timeout_when_cli_option_is_omitted(tmp_path):
    (tmp_path / "runner.py").write_text("print('accuracy: 1.0')\n", encoding="utf-8")
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        "schema: adduce/1\n"
        "smoke:\n"
        f"  command: {json.dumps(f'{sys.executable} runner.py')}\n"
        "  max_runtime_minutes: 1\n"
        "  expected_metrics: [accuracy]\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["reproduce", str(tmp_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert "timeout 1 min/run" in plain(result.output)


def test_pin_revision_codemod():
    source = (
        "from transformers import AutoModel\n"
        "model = AutoModel.from_pretrained('bert-base-uncased')\n"
        "other = AutoModel.from_pretrained('gpt2', revision='deadbeef')\n"
    )
    sha = "a" * 40
    new_source, changes = pin_revisions(source, {("hf-model", "bert-base-uncased"): sha})
    assert changes == 1
    assert f'revision="{sha}"' in new_source
    assert new_source.count("revision") == 2  # existing pin untouched


def test_pin_revision_codemod_keeps_model_and_dataset_namespaces_separate():
    source = (
        "from transformers import AutoModel\n"
        "from datasets import load_dataset\n"
        "from huggingface_hub import hf_hub_download\n"
        "model = AutoModel.from_pretrained('org/shared')\n"
        "data = load_dataset('org/shared')\n"
        "download = hf_hub_download('org/shared', 'data.csv', repo_type='dataset')\n"
    )
    model_sha = "a" * 40
    dataset_sha = "b" * 40

    new_source, changes = pin_revisions(
        source,
        {
            ("hf-model", "org/shared"): model_sha,
            ("hf-dataset", "org/shared"): dataset_sha,
        },
    )

    assert changes == 3
    assert new_source.count(f'revision="{model_sha}"') == 1
    assert new_source.count(f'revision="{dataset_sha}"') == 2


def test_pin_remotes_write_preserves_conflicting_model_dataset_namespaces(
    tmp_path,
    monkeypatch,
):
    source = (
        "from transformers import AutoModel\n"
        "from datasets import load_dataset\n"
        "model = AutoModel.from_pretrained('org/shared')\n"
        "data = load_dataset('org/shared')\n"
    )
    (tmp_path / "model.py").write_text(source, encoding="utf-8")
    model_sha = "a" * 40
    dataset_sha = "b" * 40
    monkeypatch.setattr(
        cli_module,
        "_resolve_and_print",
        lambda _result: [
            ("hf-model", "org/shared", model_sha),
            ("hf-dataset", "org/shared", dataset_sha),
        ],
    )

    result = runner.invoke(app, ["pin-remotes", str(tmp_path), "--write"])

    assert result.exit_code == 0, result.output
    updated = (tmp_path / "model.py").read_text(encoding="utf-8")
    assert f"from_pretrained('org/shared', revision=\"{model_sha}\")" in updated
    assert f"load_dataset('org/shared', revision=\"{dataset_sha}\")" in updated


def test_paper_option_enables_drift_from_external_sources(tmp_path):
    code = tmp_path / "code"
    paper = tmp_path / "paper"
    code.mkdir()
    paper.mkdir()
    _write(code, {"configs/main.yaml": "lr: 0.001\n", "train.py": "import yaml\n"})
    (paper / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}"
        "We use a learning rate of 1e-4."
        "\\end{document}",
        encoding="utf-8",
    )
    without = runner.invoke(app, ["check", str(code), "--format", "json"])
    with_paper = runner.invoke(
        app, ["check", str(code), "--paper", str(paper), "--format", "json"]
    )
    drift_without = next(
        f for f in json.loads(without.output)["findings"] if f["rule_id"] == "R-DRIFT-001"
    )
    drift_with = next(
        f for f in json.loads(with_paper.output)["findings"] if f["rule_id"] == "R-DRIFT-001"
    )
    assert drift_without["status"] == "not-applicable"
    assert drift_with["status"] == "fail"
    assert "learning_rate" in drift_with["message"]


def test_paper_option_missing_path_errors(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path), "--paper", str(tmp_path / "nope")])
    assert result.exit_code == 2


def test_no_paper_notice_in_terminal(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert "repository-only audit" in plain(result.output)


def test_findings_carry_severity(tmp_path):
    files = dict(BARE)
    files["config.yaml"] = "key: AKIAIOSFODNN7EXAMPLE\n"
    _write(tmp_path, files)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "json"])
    findings = {f["rule_id"]: f for f in json.loads(result.output)["findings"]}
    assert findings["R-PORT-004"]["severity"] == "high"  # explicit override
    assert findings["R-DET-001"]["severity"] == "high"   # derived from weight 8
    assert all("severity" in f for f in findings.values())
