"""The expanded command surface: manifest, focused audits, exports, badge,
diff, archive-plan, pin-remotes (offline half), and the codemod."""

from __future__ import annotations

import json
import subprocess
import sys

import yaml
from typer.testing import CliRunner

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


def test_pin_remotes_offline_listing(tmp_path):
    files = dict(BARE)
    files["model.py"] = "from transformers import AutoModel\nAutoModel.from_pretrained('bert-base-uncased')\n"
    _write(tmp_path, files)
    result = runner.invoke(app, ["pin-remotes", str(tmp_path)])
    assert result.exit_code == 0
    assert "without an immutable revision" in plain(result.output)
    assert "opt-in online step" in plain(result.output)  # never resolves without --diff/--write


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
    assert "Badge eligibility" in plain(chair.output)


def test_json_includes_reviewer_time_and_claims(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "json"])
    payload = json.loads(result.output)
    assert "reviewer_time" in payload and payload["reviewer_time"]["bucket"]
    assert "claims" in payload


def test_reproduce_requires_confirmation(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["reproduce", str(tmp_path), "--command", "python -c 'print(1)'"])
    assert result.exit_code == 2
    assert "--yes" in plain(result.output)


def test_reproduce_smoke_run_agrees(tmp_path):
    _write(tmp_path, WELL_FORMED)
    command = (
        f"{sys.executable} -c \"import json; json.dump({{'accuracy': 0.5}}, "
        "open('out.json','w')); print('accuracy: 0.5')\""
    )
    result = runner.invoke(
        app, ["reproduce", str(tmp_path), "--command", command, "--yes", "--timeout-minutes", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "runs agree" in plain(result.output)
    assert (tmp_path / ".adduce" / "reproduce-report.json").is_file()


def test_pin_revision_codemod():
    source = (
        "from transformers import AutoModel\n"
        "model = AutoModel.from_pretrained('bert-base-uncased')\n"
        "other = AutoModel.from_pretrained('gpt2', revision='deadbeef')\n"
    )
    sha = "a" * 40
    new_source, changes = pin_revisions(source, {"bert-base-uncased": sha})
    assert changes == 1
    assert f'revision="{sha}"' in new_source
    assert new_source.count("revision") == 2  # existing pin untouched
