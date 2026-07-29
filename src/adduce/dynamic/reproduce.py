"""``adduce reproduce``: a bounded two-run agreement check.

Runs the manifest's smoke target (or a supplied command) twice with the same
declared seed environment, fingerprints each run — expected output files hashed,
numeric values parsed from stdout — and asserts the two runs agree.

This executes repository code. It is opt-in, requires explicit confirmation,
and should be run inside a disposable, unprivileged container or virtual
machine with external isolation and resource controls. ``adduce check`` never
reaches this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO

_NUMBER_LINE_RE = re.compile(
    r"([A-Za-z][\w@/ .-]{0,40}?)[:=]\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)
_MAX_CAPTURE_BYTES = 1 << 20
_CAPTURE_CHUNK_BYTES = 64 << 10
_MAX_EXPECTED_OUTPUT_BYTES = 512 << 20
_MAX_TIMEOUT_MINUTES = 24 * 60
_TERMINATION_GRACE_SECONDS = 1.0
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{suffix}" for suffix in "123456789¹²³"),
        *(f"LPT{suffix}" for suffix in "123456789¹²³"),
    }
)


@dataclass
class _BoundedCapture:
    """Drain a pipe while retaining only its bounded tail in memory."""

    chunks: deque[bytes] = field(default_factory=deque)
    size: int = 0
    truncated: bool = False

    def consume(self, stream: IO[bytes]) -> None:
        try:
            while True:
                chunk = stream.read(_CAPTURE_CHUNK_BYTES)
                if not chunk:
                    break
                self.chunks.append(chunk)
                self.size += len(chunk)
                while self.size > _MAX_CAPTURE_BYTES:
                    overflow = self.size - _MAX_CAPTURE_BYTES
                    first = self.chunks[0]
                    if len(first) <= overflow:
                        self.chunks.popleft()
                        self.size -= len(first)
                    else:
                        self.chunks[0] = first[overflow:]
                        self.size -= overflow
                    self.truncated = True
        except (OSError, ValueError):
            # The parent may close a pipe to release a reader after a command
            # leaves background descendants behind. Captured bytes remain valid.
            return

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")


@dataclass
class RunFingerprint:
    exit_code: int
    duration_seconds: float
    output_hashes: dict[str, str] = field(default_factory=dict)   # path -> sha256
    stdout_metrics: dict[str, float] = field(default_factory=dict)
    missing_outputs: list[str] = field(default_factory=list)
    output_errors: list[str] = field(default_factory=list)
    stdout_truncated: bool = False
    stderr_truncated: bool = False


@dataclass
class ReproduceReport:
    command: str
    runs: list[RunFingerprint] = field(default_factory=list)
    agree: bool | None = None
    disagreements: list[str] = field(default_factory=list)
    comparable_fingerprints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "agree": self.agree,
            "disagreements": self.disagreements,
            "comparable_fingerprints": self.comparable_fingerprints,
            "runs": [
                {
                    "exit_code": run.exit_code,
                    "duration_seconds": round(run.duration_seconds, 1),
                    "output_hashes": run.output_hashes,
                    "stdout_metrics": run.stdout_metrics,
                    "missing_outputs": run.missing_outputs,
                    "output_errors": run.output_errors,
                    "stdout_truncated": run.stdout_truncated,
                    "stderr_truncated": run.stderr_truncated,
                }
                for run in self.runs
            ],
        }


def _hash_file(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    digest = hashlib.sha256()
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("expected output is not a regular file")
        if metadata.st_size > _MAX_EXPECTED_OUTPUT_BYTES:
            raise ValueError(
                f"expected output exceeds {_MAX_EXPECTED_OUTPUT_BYTES}-byte limit"
            )
        bytes_read = 0
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            bytes_read += len(chunk)
            if bytes_read > _MAX_EXPECTED_OUTPUT_BYTES:
                raise ValueError(
                    f"expected output exceeds {_MAX_EXPECTED_OUTPUT_BYTES}-byte limit"
                )
            digest.update(chunk)
    return digest.hexdigest()


def _parse_stdout_metrics(stdout: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in stdout.splitlines()[-200:]:
        for name, value in _NUMBER_LINE_RE.findall(line):
            key = name.strip().lower()
            if any(word in key for word in ("loss", "acc", "f1", "score", "metric", "auc", "error", "ppl", "bleu", "rouge", "ndcg")):
                try:
                    parsed = float(value)
                except ValueError:
                    continue
                if math.isfinite(parsed):
                    metrics[key] = parsed
    return metrics


def _validate_expected_outputs(expected_outputs: list[str]) -> list[str]:
    """Return configuration errors for paths that must never escape a run workspace."""
    errors: list[str] = []
    for output in expected_outputs:
        native_path = Path(output)
        posix_path = PurePosixPath(output)
        windows_path = PureWindowsPath(output)
        windows_parts = windows_path.parts[1:] if windows_path.anchor else windows_path.parts
        has_unsafe_windows_component = any(
            ":" in part
            or any(character in '<>"|?*' for character in part)
            or part.endswith((" ", "."))
            or part.rstrip(" .").partition(".")[0].upper()
            in _WINDOWS_RESERVED_NAMES
            for part in windows_parts
        )
        if (
            not output.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in output)
            or native_path.is_absolute()
            or posix_path.is_absolute()
            or bool(windows_path.anchor or windows_path.drive or windows_path.root)
            or native_path == Path(".")
            or posix_path == PurePosixPath(".")
            or windows_path == PureWindowsPath(".")
            or ".." in native_path.parts
            or ".." in posix_path.parts
            or ".." in windows_path.parts
            or has_unsafe_windows_component
        ):
            errors.append(
                f"{output or '<empty>'}: expected output must be a relative file path within the repository"
            )
    return errors


def _validate_timeout(timeout_minutes: int) -> str | None:
    if (
        isinstance(timeout_minutes, bool)
        or not isinstance(timeout_minutes, int)
        or not 1 <= timeout_minutes <= _MAX_TIMEOUT_MINUTES
    ):
        return f"timeout_minutes must be an integer from 1 to {_MAX_TIMEOUT_MINUTES}"
    return None


def _inspect_output(workspace: Path, output: str) -> tuple[str | None, str | None]:
    """Return an output hash or a stable safety error; missing files return neither."""
    relative = Path(output)
    cursor = workspace
    for part in relative.parts[:-1]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            return None, f"{output}: could not inspect expected output parent ({type(exc).__name__})"
        if stat.S_ISLNK(metadata.st_mode):
            return None, f"{output}: expected output parent is a symbolic link"
        if not stat.S_ISDIR(metadata.st_mode):
            return None, f"{output}: expected output parent is not a directory"

    target = workspace / relative
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"{output}: could not inspect expected output ({type(exc).__name__})"

    if stat.S_ISLNK(metadata.st_mode):
        return None, f"{output}: expected output is a symbolic link"
    if not stat.S_ISREG(metadata.st_mode):
        return None, f"{output}: expected output is not a regular file"
    if metadata.st_size > _MAX_EXPECTED_OUTPUT_BYTES:
        return None, f"{output}: expected output exceeds {_MAX_EXPECTED_OUTPUT_BYTES}-byte limit"

    try:
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(workspace.resolve()):
            return None, f"{output}: expected output resolves outside the run workspace"
        return _hash_file(target), None
    except (OSError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        return None, f"{output}: could not safely hash expected output ({detail})"


def _remove_copied_outputs(workspace: Path, expected_outputs: list[str]) -> None:
    """Remove only workspace copies so every attempt must generate fresh output."""
    for output in expected_outputs:
        target = workspace / output
        if target.is_symlink() or target.is_file():
            target.unlink()


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    """Skip local resolution cache and all symlinks in an isolated workspace.

    Dereferencing a repository symlink could copy data from outside the
    repository into the run workspace. Preserving it could let executed code
    mutate the original target. Neither is acceptable for the fenced runner.
    Other directories, including ``.git`` and repository-local environments,
    are copied because smoke commands may legitimately depend on them.
    """
    parent = Path(directory)
    ignored = {name for name in names if (parent / name).is_symlink()}
    if parent.name == ".adduce":
        ignored.add("cache")
    return ignored


def _metric_value(metrics: dict[str, float], expected_name: str) -> float | None:
    """Resolve an explicitly named metric without treating arbitrary stdout as evidence."""
    expected = expected_name.strip().lower()
    if expected in metrics:
        return metrics[expected]

    # Frameworks commonly prefix metrics (for example ``validation accuracy``).
    # Accept a suffix only when it identifies exactly one parsed metric.
    candidates = [
        value
        for name, value in metrics.items()
        if name.endswith(f" {expected}") or name.endswith(f"/{expected}")
    ]
    return candidates[0] if len(candidates) == 1 else None


def _signal_process_group(process: subprocess.Popen[bytes], signal_number: int) -> bool:
    """Signal only the isolated process group created for this command."""
    try:
        os.killpg(process.pid, signal_number)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if process.poll() is None:
            process.kill()
            return True
        return False


def _process_group_exists(process: subprocess.Popen[bytes]) -> bool:
    try:
        os.killpg(process.pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _terminate_remaining_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop descendants left in the command's POSIX process group after shell exit."""
    if os.name != "posix" or not _signal_process_group(process, signal.SIGTERM):
        return
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    while _process_group_exists(process) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGKILL)


def _terminate_timed_out_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        _signal_process_group(process, signal.SIGTERM)
    elif process.poll() is None:
        process.terminate()

    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_TERMINATION_GRACE_SECONDS)

    # The shell may have exited while descendants remain in its process group.
    if os.name == "posix":
        _signal_process_group(process, signal.SIGKILL)
    elif process.poll() is None:
        process.kill()
    if process.poll() is None:
        process.wait()


def _join_readers(
    threads: list[threading.Thread],
    streams: list[IO[bytes]],
    process: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + _TERMINATION_GRACE_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))

    live_threads = [thread for thread in threads if thread.is_alive()]
    if live_threads and os.name == "posix":
        # A successful shell can leave background descendants holding its pipes.
        # They are part of the isolated command group and must not outlive the run.
        _signal_process_group(process, signal.SIGTERM)
        for thread in live_threads:
            thread.join(_TERMINATION_GRACE_SECONDS)
        live_threads = [thread for thread in threads if thread.is_alive()]
        if live_threads:
            _signal_process_group(process, signal.SIGKILL)
            for thread in live_threads:
                thread.join(_TERMINATION_GRACE_SECONDS)

    for stream in streams:
        if any(thread.is_alive() for thread in threads):
            with suppress(OSError):
                os.close(stream.fileno())
        else:
            stream.close()
    for thread in threads:
        thread.join(_TERMINATION_GRACE_SECONDS)


def _run_command(
    command: str,
    root: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, _BoundedCapture, _BoundedCapture]:
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=os.name == "posix",
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("could not capture reproduction command output")

    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    streams = [process.stdout, process.stderr]
    threads = [
        threading.Thread(target=stdout_capture.consume, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr_capture.consume, args=(process.stderr,), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
        _terminate_remaining_process_group(process)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_timed_out_process(process)
    except BaseException:
        if process.poll() is None:
            _terminate_timed_out_process(process)
        raise
    finally:
        _join_readers(threads, streams, process)

    return (-1 if timed_out else process.returncode), stdout_capture, stderr_capture


def _run_once(
    command: str,
    root: Path,
    seed: int,
    expected_outputs: list[str],
    timeout_minutes: int,
) -> RunFingerprint:
    env_extra = {
        "PYTHONHASHSEED": str(seed),
        "ADDUCE_SEED": str(seed),
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
    }
    started = time.monotonic()
    exit_code, stdout_capture, stderr_capture = _run_command(
        command,
        root,
        {**os.environ, **env_extra},
        timeout_minutes * 60,
    )
    duration = time.monotonic() - started

    fingerprint = RunFingerprint(
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
    )
    fingerprint.stdout_metrics = _parse_stdout_metrics(stdout_capture.text())
    for output in expected_outputs:
        output_hash, output_error = _inspect_output(root, output)
        if output_hash is not None:
            fingerprint.output_hashes[output] = output_hash
        elif output_error is not None:
            fingerprint.output_errors.append(output_error)
        else:
            fingerprint.missing_outputs.append(output)
    return fingerprint


def reproduce(
    root: Path,
    command: str,
    expected_outputs: list[str],
    seed: int = 0,
    timeout_minutes: int = 30,
    expected_metrics: list[str] | None = None,
) -> ReproduceReport:
    """Run the command twice from one frozen repository copy and compare evidence.

    A successful process exit is necessary but not sufficient. Agreement requires
    at least one expected output hash or explicitly named metric that can be
    compared across both attempts.
    """
    report = ReproduceReport(command=command)
    expected_metrics = expected_metrics or []
    path_errors = _validate_expected_outputs(expected_outputs)
    timeout_error = _validate_timeout(timeout_minutes)
    configuration_errors = [*path_errors]
    if timeout_error is not None:
        configuration_errors.append(timeout_error)
    if configuration_errors:
        report.agree = False
        report.disagreements = configuration_errors
        return report

    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="adduce-reproduce-") as temporary:
        temporary_root = Path(temporary)
        first_workspace = temporary_root / "run-1"
        second_workspace = temporary_root / "run-2"
        # Capture the live repository once, then clone that captured tree before
        # either command starts. A first run that changes the original checkout
        # therefore cannot silently change the second run's inputs.
        shutil.copytree(
            root,
            first_workspace,
            symlinks=False,
            ignore_dangling_symlinks=True,
            ignore=_copy_ignore,
        )
        shutil.copytree(
            first_workspace,
            second_workspace,
            symlinks=False,
            ignore_dangling_symlinks=True,
            ignore=_copy_ignore,
        )
        for workspace in (first_workspace, second_workspace):
            _remove_copied_outputs(workspace, expected_outputs)
            report.runs.append(
                _run_once(command, workspace, seed, expected_outputs, timeout_minutes)
            )

    first, second = report.runs
    disagreements: list[str] = []
    if first.exit_code != 0 or second.exit_code != 0:
        disagreements.append(
            f"non-zero exit codes (run 1: {first.exit_code}, run 2: {second.exit_code})"
        )
    for index, run in enumerate(report.runs, start=1):
        disagreements.extend(f"run {index}: {error}" for error in run.output_errors)
    for output in expected_outputs:
        hash_one = first.output_hashes.get(output)
        hash_two = second.output_hashes.get(output)
        if hash_one is None or hash_two is None:
            disagreements.append(f"{output}: not produced by both runs")
        elif hash_one != hash_two:
            disagreements.append(f"{output}: content differs between runs")
        else:
            report.comparable_fingerprints.append(f"output:{output}")
    for name in expected_metrics:
        value_one = _metric_value(first.stdout_metrics, name)
        value_two = _metric_value(second.stdout_metrics, name)
        if value_one is None or value_two is None:
            disagreements.append(f"expected stdout metric '{name}': not reported by both runs")
        elif abs(value_one - value_two) > 1e-9:
            disagreements.append(
                f"stdout metric '{name}': {value_one} vs {value_two}"
            )
        else:
            report.comparable_fingerprints.append(f"metric:{name}")
    if not report.comparable_fingerprints:
        disagreements.append(
            "no comparable fingerprints: declare an expected output or expected metric "
            "that both runs produce"
        )
    report.disagreements = disagreements
    report.agree = not disagreements
    return report


def _validate_report_destination(directory: Path, target: Path) -> None:
    try:
        directory_metadata = directory.lstat()
    except FileNotFoundError:
        with suppress(FileExistsError):
            directory.mkdir(mode=0o700)
        directory_metadata = directory.lstat()

    if stat.S_ISLNK(directory_metadata.st_mode):
        raise ValueError("refusing to write through a symbolic-link .adduce directory")
    if not stat.S_ISDIR(directory_metadata.st_mode):
        raise ValueError("refusing to write because .adduce is not a directory")

    try:
        target_metadata = target.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(target_metadata.st_mode):
        raise ValueError("refusing to replace a symbolic-link reproduction report")
    if not stat.S_ISREG(target_metadata.st_mode):
        raise ValueError("refusing to replace a non-regular reproduction report")


def _write_report_posix(directory: Path, target_name: str, payload: bytes) -> None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = os.open(directory, directory_flags)
    temporary_name = f".{target_name}.{secrets.token_hex(8)}.tmp"
    temporary_created = False
    try:
        try:
            target_metadata = os.stat(
                target_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(target_metadata.st_mode):
                raise ValueError("refusing to replace a symbolic-link reproduction report")
            if not stat.S_ISREG(target_metadata.st_mode):
                raise ValueError("refusing to replace a non-regular reproduction report")

        temporary_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temporary_flags |= os.O_NOFOLLOW
        descriptor = os.open(
            temporary_name,
            temporary_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_created = True
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        os.rename(
            temporary_name,
            target_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_created = False
        with suppress(OSError):
            os.fsync(directory_descriptor)
    finally:
        if temporary_created:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.close(directory_descriptor)


def _write_report_portable(directory: Path, target: Path, payload: bytes) -> None:
    temporary = directory / f".{target.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def save_report(root: Path, report: ReproduceReport) -> Path:
    directory = root / ".adduce"
    target = directory / "reproduce-report.json"
    payload = (
        json.dumps(report.to_dict(), allow_nan=False, indent=2) + "\n"
    ).encode("utf-8")
    _validate_report_destination(directory, target)
    if os.name == "posix":
        _write_report_posix(directory, target.name, payload)
    else:
        _write_report_portable(directory, target, payload)
    return target
