"""Real-process colour regression: FORCE_COLOR / NO_COLOR must still reach a
genuine user. tests/conftest.py strips the forcing vars before adduce.cli's
module-level Console is built, so no in-process CliRunner test can see this
path; only a subprocess with an explicit environment can."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import pytest

_ANSI_RE = re.compile(rb"\x1b\[[0-9;]*m")


def _adduce_executable():
    venv_local = os.path.join(os.path.dirname(sys.executable), "adduce.exe" if os.name == "nt" else "adduce")
    if os.path.isfile(venv_local):
        return venv_local
    return shutil.which("adduce")


def _run_check(tmp_path, env):
    (tmp_path / "train.py").write_text("print('hi')\n", encoding="utf-8")
    executable = _adduce_executable()
    return subprocess.run(
        [executable, "check", str(tmp_path)],
        env=env,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(os.name == "nt", reason="ANSI-over-a-pipe behaviour asserted here is POSIX-specific")
def test_force_color_and_no_color_reach_the_real_cli(tmp_path):
    executable = _adduce_executable()
    if executable is None:
        pytest.skip("adduce console script is not installed")

    base_env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}

    forced = _run_check(tmp_path, {**base_env, "FORCE_COLOR": "3"})
    assert forced.returncode == 0, forced.stderr
    assert _ANSI_RE.search(forced.stdout), forced.stdout

    unforced = _run_check(tmp_path, {**base_env, "NO_COLOR": "1"})
    assert unforced.returncode == 0, unforced.stderr
    assert not _ANSI_RE.search(unforced.stdout), unforced.stdout
