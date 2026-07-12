"""Safety and evidence requirements for opt-in dynamic reproduction."""

from __future__ import annotations

import shlex
import subprocess
import sys

from adduce.dynamic.reproduce import reproduce


def _python_command(script: str = "runner.py") -> str:
    arguments = [sys.executable, script]
    if sys.platform == "win32":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def test_python_command_uses_windows_shell_quoting(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    command = _python_command("runner with spaces.py")

    assert command == subprocess.list2cmdline([sys.executable, "runner with spaces.py"])


def test_reproduce_isolates_runs_and_preserves_existing_files(tmp_path):
    (tmp_path / "input.txt").write_text("author input\n", encoding="utf-8")
    (tmp_path / "result.json").write_text("author result\n", encoding="utf-8")
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\n"
        "Path('input.txt').write_text('changed by run\\n', encoding='utf-8')\n"
        "Path('result.json').write_text('{\"accuracy\": 0.5}\\n', encoding='utf-8')\n"
        "print('accuracy: 0.5')\n",
        encoding="utf-8",
    )

    report = reproduce(
        tmp_path,
        _python_command(),
        ["result.json"],
        timeout_minutes=1,
        expected_metrics=["accuracy"],
    )

    assert report.agree is True
    assert report.comparable_fingerprints == ["output:result.json", "metric:accuracy"]
    assert (tmp_path / "input.txt").read_text(encoding="utf-8") == "author input\n"
    assert (tmp_path / "result.json").read_text(encoding="utf-8") == "author result\n"


def test_preexisting_output_does_not_count_as_run_output(tmp_path):
    (tmp_path / "result.json").write_text("pre-existing\n", encoding="utf-8")
    (tmp_path / "runner.py").write_text("print('completed')\n", encoding="utf-8")

    report = reproduce(
        tmp_path,
        _python_command(),
        ["result.json"],
        timeout_minutes=1,
    )

    assert report.agree is False
    assert "result.json: not produced by both runs" in report.disagreements
    assert any("no comparable fingerprints" in item for item in report.disagreements)
    assert (tmp_path / "result.json").read_text(encoding="utf-8") == "pre-existing\n"


def test_two_zero_exits_without_expected_evidence_do_not_agree(tmp_path):
    (tmp_path / "runner.py").write_text("print('loss: 0.25')\n", encoding="utf-8")

    report = reproduce(tmp_path, _python_command(), [], timeout_minutes=1)

    assert [run.exit_code for run in report.runs] == [0, 0]
    assert report.agree is False
    assert report.comparable_fingerprints == []
    assert any("no comparable fingerprints" in item for item in report.disagreements)


def test_explicit_expected_metric_is_comparable_evidence(tmp_path):
    (tmp_path / "runner.py").write_text("print('validation accuracy: 5e-1')\n", encoding="utf-8")

    report = reproduce(
        tmp_path,
        _python_command(),
        [],
        timeout_minutes=1,
        expected_metrics=["accuracy"],
    )

    assert report.agree is True
    assert report.comparable_fingerprints == ["metric:accuracy"]


def test_unsafe_expected_output_path_is_rejected_without_execution(tmp_path):
    marker = tmp_path / "executed"
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('executed').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )

    report = reproduce(tmp_path, _python_command(), ["../outside.json"], timeout_minutes=1)

    assert report.agree is False
    assert report.runs == []
    assert "relative file path" in report.disagreements[0]
    assert not marker.exists()


def test_reproduce_workspace_preserves_git_metadata(tmp_path):
    (tmp_path / "runner.py").write_text(
        "import subprocess\n"
        "from pathlib import Path\n"
        "head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
        "Path('head.txt').write_text(head + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "runner.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )

    report = reproduce(tmp_path, _python_command(), ["head.txt"], timeout_minutes=1)

    assert report.agree is True
    assert report.comparable_fingerprints == ["output:head.txt"]
