"""Safety and evidence requirements for opt-in dynamic reproduction."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time

import pytest

import adduce.dynamic.reproduce as reproduce_module
from adduce.dynamic.reproduce import ReproduceReport, reproduce, save_report


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


def test_both_runs_use_one_snapshot_when_first_run_changes_original(tmp_path):
    original_input = tmp_path / "input.txt"
    original_input.write_text("frozen input\n", encoding="utf-8")
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\n"
        f"original = Path({str(original_input)!r})\n"
        "Path('result.txt').write_text(Path('input.txt').read_text(encoding='utf-8'), "
        "encoding='utf-8')\n"
        "original.write_text('changed original\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    report = reproduce(
        tmp_path,
        _python_command(),
        ["result.txt"],
        timeout_minutes=1,
    )

    assert report.agree is True
    assert report.comparable_fingerprints == ["output:result.txt"]
    assert original_input.read_text(encoding="utf-8") == "changed original\n"


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


def test_non_finite_stdout_metric_is_not_comparable_evidence(tmp_path):
    (tmp_path / "runner.py").write_text(
        "print('accuracy: 1e999')\n", encoding="utf-8"
    )

    report = reproduce(
        tmp_path,
        _python_command(),
        [],
        timeout_minutes=1,
        expected_metrics=["accuracy"],
    )

    assert report.agree is False
    assert report.runs[0].stdout_metrics == {}
    assert report.runs[1].stdout_metrics == {}
    assert report.disagreements == [
        "expected stdout metric 'accuracy': not reported by both runs",
        "no comparable fingerprints: declare an expected output or expected metric "
        "that both runs produce",
    ]


def test_report_serialization_rejects_non_finite_values(tmp_path):
    report = ReproduceReport(command="example")
    report.runs.append(
        reproduce_module.RunFingerprint(exit_code=0, duration_seconds=float("inf"))
    )

    with pytest.raises(ValueError, match="Out of range float"):
        save_report(tmp_path, report)

    assert not (tmp_path / ".adduce" / "reproduce-report.json").exists()


@pytest.mark.parametrize(
    "unsafe_output",
    [
        "../outside.json",
        "/outside.json",
        r"\outside.json",
        r"C:\outside.json",
        r"C:outside.json",
        r"result.json:stream",
        "NUL",
        "result.json. ",
    ],
)
def test_unsafe_expected_output_path_is_rejected_without_execution(
    tmp_path, unsafe_output
):
    marker = tmp_path / "executed"
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('executed').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )

    report = reproduce(tmp_path, _python_command(), [unsafe_output], timeout_minutes=1)

    assert report.agree is False
    assert report.runs == []
    assert "relative file path" in report.disagreements[0]
    assert not marker.exists()


@pytest.mark.parametrize("timeout_minutes", [0, -1, 1441, True])
def test_invalid_timeout_is_rejected_without_execution(tmp_path, timeout_minutes):
    marker = tmp_path / "executed"
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('executed').write_text('yes', encoding='utf-8')\n",
        encoding="utf-8",
    )

    report = reproduce(
        tmp_path,
        _python_command(),
        [],
        timeout_minutes=timeout_minutes,
    )

    assert report.agree is False
    assert report.runs == []
    assert report.disagreements == ["timeout_minutes must be an integer from 1 to 1440"]
    assert not marker.exists()


def test_capture_is_bounded_and_keeps_metrics_from_stdout_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(reproduce_module, "_MAX_CAPTURE_BYTES", 256)
    (tmp_path / "runner.py").write_text(
        "import os\n"
        "os.write(1, b'x' * 4096 + b'\\naccuracy: 0.5\\n')\n"
        "os.write(2, b'y' * 4096 + b'\\nloss: 0.25\\n')\n",
        encoding="utf-8",
    )

    report = reproduce(
        tmp_path,
        _python_command(),
        [],
        timeout_minutes=1,
        expected_metrics=["accuracy"],
    )

    assert report.agree is True
    assert report.comparable_fingerprints == ["metric:accuracy"]
    assert all("loss" not in run.stdout_metrics for run in report.runs)
    assert all(run.stdout_truncated for run in report.runs)
    assert all(run.stderr_truncated for run in report.runs)
    serialized_runs = report.to_dict()["runs"]
    assert all(run["stdout_truncated"] is True for run in serialized_runs)
    assert all(run["stderr_truncated"] is True for run in serialized_runs)


def test_symbolic_link_output_is_rejected_before_hashing(tmp_path):
    (tmp_path / "input.txt").write_text("outside output\n", encoding="utf-8")
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('result.json').symlink_to('input.txt')\n",
        encoding="utf-8",
    )

    report = reproduce(tmp_path, _python_command(), ["result.json"], timeout_minutes=1)

    assert report.agree is False
    assert all(not run.output_hashes for run in report.runs)
    assert all(
        run.output_errors == ["result.json: expected output is a symbolic link"]
        for run in report.runs
    )
    assert "run 1: result.json: expected output is a symbolic link" in report.disagreements


def test_non_regular_output_is_rejected_before_hashing(tmp_path):
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('result.json').mkdir()\n",
        encoding="utf-8",
    )

    report = reproduce(tmp_path, _python_command(), ["result.json"], timeout_minutes=1)

    assert report.agree is False
    assert all(
        run.output_errors == ["result.json: expected output is not a regular file"]
        for run in report.runs
    )


def test_oversized_output_is_rejected_before_hashing(tmp_path, monkeypatch):
    monkeypatch.setattr(reproduce_module, "_MAX_EXPECTED_OUTPUT_BYTES", 4)
    (tmp_path / "runner.py").write_text(
        "from pathlib import Path\nPath('result.json').write_bytes(b'12345')\n",
        encoding="utf-8",
    )

    report = reproduce(tmp_path, _python_command(), ["result.json"], timeout_minutes=1)

    assert report.agree is False
    assert all(not run.output_hashes for run in report.runs)
    assert all(
        run.output_errors == ["result.json: expected output exceeds 4-byte limit"]
        for run in report.runs
    )


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


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_timeout_kills_descendant_process_group(tmp_path):
    marker = tmp_path / "descendant-survived"
    child = (
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"time.sleep(2); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    (tmp_path / "runner.py").write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    exit_code, _, _ = reproduce_module._run_command(
        _python_command(),
        tmp_path,
        dict(os.environ),
        timeout_seconds=1,
    )
    time.sleep(1.25)

    assert exit_code == -1
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_successful_shell_cannot_leave_background_descendant(tmp_path):
    marker = tmp_path / "background-descendant-survived"
    child = (
        "import time; from pathlib import Path; "
        f"time.sleep(1); Path({str(marker)!r}).write_text('alive', encoding='utf-8')"
    )
    command = f"{shlex.join([sys.executable, '-c', child])} >/dev/null 2>&1 & true"

    exit_code, _, _ = reproduce_module._run_command(
        command,
        tmp_path,
        dict(os.environ),
        timeout_seconds=10,
    )
    time.sleep(1.25)

    assert exit_code == 0
    assert not marker.exists()


def test_hash_file_and_portable_report_write_pass_binary_flag(tmp_path, monkeypatch):
    # The O_BINARY term is only meaningful on Windows, so this only regresses
    # if a call site stops routing through _open_flags and re-inlines its own
    # flags. Simulate the attribute and capture what os.open actually receives.
    native_binary = getattr(os, "O_BINARY", 0)
    sentinel = native_binary or 0x8000
    if not native_binary:
        monkeypatch.setattr(os, "O_BINARY", sentinel, raising=False)
    real_open = os.open
    captured: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        captured.append(flags)
        # Where O_BINARY is native the flag must still reach the OS, or the
        # writes below fail in exactly the way the flag exists to prevent.
        delivered = flags if native_binary else flags & ~sentinel
        return real_open(path, delivered, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)

    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"line one\nline two\n")
    reproduce_module._hash_file(sample)
    assert captured
    assert all(flags & sentinel == sentinel for flags in captured)

    captured.clear()
    directory = tmp_path / "portable-report"
    directory.mkdir()
    target = directory / "reproduce-report.json"
    reproduce_module._write_report_portable(directory, target, b'{"agree": true}\n')
    assert captured
    assert all(flags & sentinel == sentinel for flags in captured)
    assert target.read_bytes() == b'{"agree": true}\n'


@pytest.mark.skipif(os.name != "posix", reason="POSIX dir_fd based writer")
def test_posix_report_write_passes_binary_flag_for_temporary_file(tmp_path, monkeypatch):
    native_binary = getattr(os, "O_BINARY", 0)
    sentinel = native_binary or 0x8000
    if not native_binary:
        monkeypatch.setattr(os, "O_BINARY", sentinel, raising=False)
    real_open = os.open
    captured: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        captured.append(flags)
        # Where O_BINARY is native the flag must still reach the OS, or the
        # writes below fail in exactly the way the flag exists to prevent.
        delivered = flags if native_binary else flags & ~sentinel
        return real_open(path, delivered, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy_open)

    directory = tmp_path / ".adduce"
    directory.mkdir()
    reproduce_module._write_report_posix(
        directory, "reproduce-report.json", b'{"agree": true}\n'
    )

    # The directory-descriptor open at the top of _write_report_posix has no
    # data flowing through it and never runs on the platform O_BINARY exists
    # for (it uses O_DIRECTORY, POSIX-only), so it is excluded on purpose;
    # only the temporary-file open (identified by O_CREAT) is asserted here.
    temporary_file_flags = [flags for flags in captured if flags & os.O_CREAT]
    assert temporary_file_flags
    assert all(flags & sentinel == sentinel for flags in temporary_file_flags)
    assert (directory / "reproduce-report.json").read_bytes() == b'{"agree": true}\n'


def test_save_report_replaces_regular_file_atomically(tmp_path):
    directory = tmp_path / ".adduce"
    directory.mkdir()
    target = directory / "reproduce-report.json"
    target.write_text("old\n", encoding="utf-8")
    report = ReproduceReport(command="python runner.py", agree=True)

    saved = save_report(tmp_path, report)

    assert saved == target
    assert json.loads(target.read_text(encoding="utf-8"))["agree"] is True
    assert not list(directory.glob(".reproduce-report.json.*.tmp"))


def test_save_report_rejects_symbolic_link_directory(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / ".adduce").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic-link .adduce"):
        save_report(tmp_path, ReproduceReport(command="true"))

    assert not (external / "reproduce-report.json").exists()


def test_save_report_rejects_symbolic_link_target(tmp_path):
    directory = tmp_path / ".adduce"
    directory.mkdir()
    external = tmp_path / "external.json"
    external.write_text("preserve\n", encoding="utf-8")
    (directory / "reproduce-report.json").symlink_to(external)

    with pytest.raises(ValueError, match="symbolic-link reproduction report"):
        save_report(tmp_path, ReproduceReport(command="true"))

    assert external.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic writer")
def test_save_report_failure_preserves_previous_report(tmp_path, monkeypatch):
    directory = tmp_path / ".adduce"
    directory.mkdir()
    target = directory / "reproduce-report.json"
    target.write_text("old\n", encoding="utf-8")

    def fail_rename(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(reproduce_module.os, "rename", fail_rename)

    with pytest.raises(OSError, match="simulated rename failure"):
        save_report(tmp_path, ReproduceReport(command="true"))

    assert target.read_text(encoding="utf-8") == "old\n"
    assert not list(directory.glob(".reproduce-report.json.*.tmp"))


def test_a_git_lock_file_is_not_carried_into_a_workspace(tmp_path):
    """A lock exists only while a git process holds something open.

    Git runs auto-maintenance in the background after ordinary commands, so one
    of these can appear and vanish while the workspace is being captured. It is
    also the one file that must not arrive in the copy: a stale lock makes git
    in the workspace refuse to work.
    """
    objects = tmp_path / ".git" / "objects"
    objects.mkdir(parents=True)
    (objects / "maintenance.lock").write_text("", encoding="utf-8")
    (objects / "pack").mkdir()

    ignored = reproduce_module._copy_ignore(str(objects), ["maintenance.lock", "pack"])

    assert ignored == {"maintenance.lock"}


def test_a_source_that_disappears_mid_copy_does_not_abandon_the_run(tmp_path):
    """`shutil.copytree` lists a directory and then copies it.

    Anything that goes in between — a lock, an editor's temporary file, a build
    artifact — otherwise raises at the end of the whole copy, losing the
    reproduction over a transient that has nothing to do with the repository
    under test.
    """
    destination = tmp_path / "copied.txt"

    reproduce_module._copy_tolerating_disappearance(
        str(tmp_path / "never-existed.txt"), str(destination)
    )

    assert not destination.exists()

    source = tmp_path / "real.txt"
    source.write_text("kept\n", encoding="utf-8")
    reproduce_module._copy_tolerating_disappearance(str(source), str(destination))

    assert destination.read_text(encoding="utf-8") == "kept\n"
