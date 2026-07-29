"""First-use RNG diagnostic state, wrapping, and process behavior."""

from __future__ import annotations

import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from adduce.dynamic import import_hook

ROOT = Path(__file__).resolve().parents[1]


def _entrypoint() -> str:
    executable = shutil.which("adduce-rng-audit")
    assert executable is not None, "install the project so its console entry points are available"
    return executable


def _run_hook(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _entrypoint(),
            "--yes",
            str(script),
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_import_hook_refuses_execution_without_explicit_confirmation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "executed"
    script = tmp_path / "target.py"
    script.write_text(
        "from pathlib import Path\nPath('executed').touch()\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [_entrypoint(), str(script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "executes the target script unsandboxed" in completed.stderr
    assert "refusing execution without explicit --yes" in completed.stderr
    assert not marker.exists()


def test_import_hook_warns_even_when_no_target_is_given() -> None:
    completed = subprocess.run(
        [_entrypoint()],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "executes the target script unsandboxed" in completed.stderr
    assert "--yes <script.py>" in completed.stderr


def test_console_entrypoint_cannot_be_shadowed_by_repository_package(tmp_path: Path) -> None:
    marker = tmp_path / "shadow-package-imported"
    shadow = tmp_path / "adduce"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    script = tmp_path / "target.py"
    script.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")

    completed = subprocess.run(
        [_entrypoint(), str(script)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "refusing execution without explicit --yes" in completed.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    ("source", "expected_code", "expected_summary"),
    [
        (
            "import random\nrandom.random()\n",
            1,
            "UNCONTROLLED RNG USE detected",
        ),
        (
            "import random\nrandom.seed(7)\nrandom.random()\n",
            0,
            "deterministic seeding preceded all observed draws",
        ),
    ],
)
def test_import_hook_reports_stdlib_ordering(
    tmp_path: Path,
    source: str,
    expected_code: int,
    expected_summary: str,
) -> None:
    script = tmp_path / "target.py"
    script.write_text(source, encoding="utf-8")

    completed = _run_hook(script)

    assert completed.returncode == expected_code
    assert expected_summary in completed.stderr
    assert "[adduce order] done:" in completed.stderr


def test_import_hook_preserves_target_system_exit_and_arguments(tmp_path: Path) -> None:
    script = tmp_path / "target.py"
    script.write_text(
        "import sys\n"
        "assert sys.argv[1:] == ['alpha', 'two words']\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )

    completed = _run_hook(script, "alpha", "two words")

    assert completed.returncode == 7
    assert "done: 0 first-use event(s); no RNG calls observed" in completed.stderr


@pytest.mark.parametrize(("target_code", "expected_code"), [(0, 1), (7, 7)])
def test_uncontrolled_rng_overrides_only_successful_target_exit(
    tmp_path: Path,
    target_code: int,
    expected_code: int,
) -> None:
    script = tmp_path / "target.py"
    script.write_text(
        "import random\n"
        "random.random()\n"
        f"raise SystemExit({target_code})\n",
        encoding="utf-8",
    )

    completed = _run_hook(script)

    assert completed.returncode == expected_code
    assert "UNCONTROLLED RNG USE detected" in completed.stderr


def test_failed_stdlib_seed_is_not_recorded(tmp_path: Path) -> None:
    script = tmp_path / "target.py"
    script.write_text(
        "import random\n"
        "try:\n"
        "    random.seed(object())\n"
        "except TypeError:\n"
        "    pass\n"
        "random.random()\n",
        encoding="utf-8",
    )

    completed = _run_hook(script)

    assert completed.returncode == 1
    assert "seed: random.seed" not in completed.stderr
    assert "first draw (random.random) before a seed" in completed.stderr


def _function(name: str):
    def implementation(*_args, **_kwargs):
        return name

    return implementation


def _install_fake_rng_modules(monkeypatch) -> tuple[ModuleType, ModuleType, ModuleType]:
    fake_random = ModuleType("random")
    for name in (
        "seed",
        "random",
        "randint",
        "randrange",
        "shuffle",
        "sample",
        "choice",
        "uniform",
        "gauss",
    ):
        setattr(fake_random, name, _function(name))

    fake_numpy = ModuleType("numpy")
    fake_numpy.random = SimpleNamespace(  # type: ignore[attr-defined]
        **{
            name: _function(name)
            for name in (
                "seed",
                "default_rng",
                "rand",
                "randn",
                "randint",
                "random",
                "shuffle",
                "permutation",
                "choice",
                "normal",
                "uniform",
            )
        }
    )
    fake_torch = ModuleType("torch")
    for name in (
        "manual_seed",
        "rand",
        "randn",
        "randint",
        "randperm",
        "normal",
        "bernoulli",
        "multinomial",
    ):
        setattr(fake_torch, name, _function(name))

    monkeypatch.setitem(sys.modules, "random", fake_random)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    return fake_random, fake_numpy, fake_torch


def test_install_is_reentrant_and_default_rng_classification_is_argument_sensitive(
    monkeypatch,
) -> None:
    fake_random, fake_numpy, _fake_torch = _install_fake_rng_modules(monkeypatch)

    import_hook.LOG.reset()
    try:
        assert import_hook.install() is import_hook.LOG
        first_wrapper = fake_random.random  # type: ignore[attr-defined]
        import_hook.install()
        assert fake_random.random is first_wrapper  # type: ignore[attr-defined]

        fake_numpy.random.default_rng()  # type: ignore[attr-defined]
        fake_numpy.random.default_rng(seed=123)  # type: ignore[attr-defined]

        assert import_hook.LOG.draw_before_seed is False
        assert import_hook.LOG.uncontrolled is True
        assert import_hook.LOG.seeded is True
        assert import_hook.LOG.seeded_families == {"numpy-generator"}
        assert import_hook.LOG.draw_before_seed_families == set()
        assert import_hook.LOG.entropy_seed_families == {"numpy-generator"}
        assert [event for _, event in import_hook.LOG.events] == [
            "entropy: numpy.random.default_rng",
            "seed: numpy.random.default_rng",
        ]
    finally:
        import_hook.LOG.reset()


def test_seeding_is_scoped_to_each_rng_family(monkeypatch, capsys) -> None:
    fake_random, fake_numpy, fake_torch = _install_fake_rng_modules(monkeypatch)

    import_hook.LOG.reset()
    try:
        import_hook.install()

        fake_random.seed(7)  # type: ignore[attr-defined]
        fake_numpy.random.default_rng(seed=123)  # type: ignore[attr-defined]
        fake_numpy.random.random()  # type: ignore[attr-defined]
        fake_torch.rand(1)  # type: ignore[attr-defined]

        assert import_hook.LOG.seeded_families == {"python", "numpy-generator"}
        assert import_hook.LOG.draw_before_seed_families == {"numpy-global", "torch"}
        diagnostic = capsys.readouterr().err
        assert "before a seed for RNG family numpy-global" in diagnostic
        assert "before a seed for RNG family torch" in diagnostic
    finally:
        import_hook.LOG.reset()


def test_none_seed_is_reported_as_entropy_not_deterministic_seed(
    monkeypatch, capsys
) -> None:
    fake_random, fake_numpy, _fake_torch = _install_fake_rng_modules(monkeypatch)

    import_hook.LOG.reset()
    try:
        import_hook.install()

        fake_random.seed()  # type: ignore[attr-defined]
        fake_numpy.random.seed(None)  # type: ignore[attr-defined]
        fake_random.random()  # type: ignore[attr-defined]
        fake_numpy.random.random()  # type: ignore[attr-defined]

        assert import_hook.LOG.seeded_families == set()
        assert import_hook.LOG.entropy_seed_families == {"python", "numpy-global"}
        assert import_hook.LOG.draw_before_seed_families == {"python", "numpy-global"}
        assert import_hook.LOG.uncontrolled is True
        diagnostic = capsys.readouterr().err
        assert "entropy-based seed (random.seed)" in diagnostic
        assert "entropy-based seed (numpy.random.seed)" in diagnostic
    finally:
        import_hook.LOG.reset()


def test_failed_rng_calls_are_not_recorded(monkeypatch) -> None:
    fake_random, fake_numpy, fake_torch = _install_fake_rng_modules(monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError("seed failed")

    fake_numpy.random.seed = fail  # type: ignore[attr-defined]
    fake_numpy.random.default_rng = fail  # type: ignore[attr-defined]
    fake_torch.manual_seed = fail  # type: ignore[attr-defined]
    fake_random.random = fail  # type: ignore[attr-defined]

    import_hook.LOG.reset()
    try:
        import_hook.install()

        with pytest.raises(RuntimeError, match="seed failed"):
            fake_numpy.random.seed(None)  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="seed failed"):
            fake_numpy.random.default_rng(seed=123)  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="seed failed"):
            fake_torch.manual_seed(123)  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="seed failed"):
            fake_random.random()  # type: ignore[attr-defined]

        assert import_hook.LOG.events == []
        assert import_hook.LOG.seeded_families == set()
        assert import_hook.LOG.entropy_seed_families == set()
        assert import_hook.LOG.uncontrolled is False
    finally:
        import_hook.LOG.reset()


def test_main_resets_shared_state_and_restores_argv(monkeypatch, capsys) -> None:
    original_argv = ["import-hook", "--yes", "target.py", "argument"]
    import_hook.LOG.record("draw", "stale-event")
    monkeypatch.setattr(sys, "argv", original_argv)
    monkeypatch.setattr(import_hook, "install", lambda: import_hook.LOG)

    def fake_run_path(script: str, *, run_name: str) -> None:
        assert script == "target.py"
        assert run_name == "__main__"
        assert sys.argv == ["target.py", "argument"]
        assert import_hook.LOG.events == []
        import_hook.LOG.record("seed", "random.seed")
        import_hook.LOG.record("draw", "random.random")

    monkeypatch.setattr(runpy, "run_path", fake_run_path)

    assert import_hook.main() == 0
    assert sys.argv is original_argv
    assert "done: 2 first-use event(s)" in capsys.readouterr().err
    import_hook.LOG.reset()
