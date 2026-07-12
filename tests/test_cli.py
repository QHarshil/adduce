"""CLI behaviour: formats, exit codes, scaffolds, and informational commands."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from adduce.cli import app
from tests.test_engine import BARE, WELL_FORMED, _write

runner = CliRunner()


def test_check_terminal_output(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert result.exit_code == 0
    assert "Reproducibility" in result.output


def test_check_json_format(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["tool"]["name"] == "adduce"
    assert 0 <= payload["total"] <= 100


def test_check_sarif_is_valid_json(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "sarif"])
    assert result.exit_code == 0
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]  # a bare repo produces findings


def test_check_badge_format(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["check", str(tmp_path), "--format", "badge"])
    badge = json.loads(result.output)
    assert badge["schemaVersion"] == 1
    assert badge["label"] == "reproducibility"


def test_fail_under_exit_code(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path), "--fail-under", "95"])
    assert result.exit_code == 1


def test_diagnostic_by_default(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path)])
    assert result.exit_code == 0


def test_unknown_profile_errors(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["check", str(tmp_path), "--profile", "nope"])
    assert result.exit_code == 2


def test_baseline_then_regression_gate(tmp_path):
    _write(tmp_path, WELL_FORMED)
    assert runner.invoke(app, ["baseline", str(tmp_path)]).exit_code == 0
    assert (tmp_path / ".adduce" / "baseline.json").is_file()
    # Unchanged repo: no regression.
    result = runner.invoke(app, ["check", str(tmp_path), "--fail-on-regression"])
    assert result.exit_code == 0
    # Degrade and re-check.
    (tmp_path / "train.py").write_text("import torch\n", encoding="utf-8")
    result = runner.invoke(app, ["check", str(tmp_path), "--fail-on-regression"])
    assert result.exit_code == 1


def test_checklist_command(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["checklist", str(tmp_path), "--profile", "neurips"])
    assert result.exit_code == 0
    assert "NeurIPS" in result.output
    assert "Answer:" in result.output


def test_fix_scaffold_seeds(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["fix", str(tmp_path), "--scaffold", "seeds"])
    assert result.exit_code == 0
    content = (tmp_path / "seed_utils.py").read_text(encoding="utf-8")
    assert "torch.cuda.manual_seed_all" in content
    assert "worker" in content
    # Generated file must be valid Python.
    compile(content, "seed_utils.py", "exec")
    # Idempotent: second run refuses to overwrite.
    rerun = runner.invoke(app, ["fix", str(tmp_path), "--scaffold", "seeds"])
    assert "skipped" in rerun.output
    forced = runner.invoke(app, ["fix", str(tmp_path), "--scaffold", "seeds", "--force"])
    assert forced.exit_code == 0
    assert "deprecated" in forced.output
    assert "skipped" in forced.output


def test_fix_by_rule_id(tmp_path):
    _write(tmp_path, BARE)
    result = runner.invoke(app, ["fix", str(tmp_path), "--rule", "R-LIC-002"])
    assert result.exit_code == 0
    assert (tmp_path / "CITATION.cff").is_file()


def test_fix_readme_appends_missing_sections(tmp_path):
    files = dict(BARE)
    files["README.md"] = "# Demo\n\n## Installation\n\npip install .\n"
    _write(tmp_path, files)
    result = runner.invoke(app, ["fix", str(tmp_path), "--scaffold", "readme"])
    assert result.exit_code == 0
    content = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert content.startswith("# Demo")  # existing content untouched
    assert "Expected results" in content
    assert "[AUTHOR REVIEW REQUIRED] Define the acceptable metric tolerance" in content
    assert "successful reproduction" not in content
    assert content.count("## Installation") == 1  # present section not duplicated


def test_rules_and_explain(tmp_path):
    listing = runner.invoke(app, ["rules"])
    assert "R-DET-001" in listing.output
    explain = runner.invoke(app, ["explain", "R-DET-001"])
    assert explain.exit_code == 0
    assert "ignore=R-DET-001" in explain.output
    unknown = runner.invoke(app, ["explain", "R-NOPE-999"])
    assert unknown.exit_code == 2


def test_check_output_to_file(tmp_path):
    _write(tmp_path, WELL_FORMED)
    out = tmp_path / "report.md"
    result = runner.invoke(
        app, ["check", str(tmp_path), "--format", "markdown", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert "Reproducibility report" in out.read_text(encoding="utf-8")
