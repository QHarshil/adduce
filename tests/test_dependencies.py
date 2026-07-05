"""Dependency manifest parsing and pin-level classification."""

from __future__ import annotations

from adduce.evidence.dependencies import PinLevel


def test_requirements_pin_levels(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": (
                "torch==2.1.0\n"
                "numpy~=1.26\n"
                "pandas>=2.0,<3.0\n"
                "scipy>=1.0\n"
                "requests\n"
                "# a comment\n"
            ),
            "main.py": "print('hi')\n",
        }
    )
    deps = {d.name: d.pin for d in ev.deps.dependencies}
    assert deps["torch"] is PinLevel.EXACT
    assert deps["numpy"] is PinLevel.BOUNDED
    assert deps["pandas"] is PinLevel.BOUNDED
    assert deps["scipy"] is PinLevel.UNBOUNDED
    assert deps["requests"] is PinLevel.UNBOUNDED
    assert 0.0 < ev.deps.pinned_fraction < 1.0


def test_lockfile_counts_as_fully_pinned(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch\n",
            "uv.lock": "",
            "main.py": "pass\n",
        }
    )
    assert ev.deps.has_lockfile
    assert ev.deps.pinned_fraction == 1.0


def test_pyproject_dependencies_and_python_version(make_evidence):
    ev = make_evidence(
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "demo"\n'
                'version = "0.1"\n'
                'requires-python = ">=3.10"\n'
                'dependencies = ["torch==2.1.0", "numpy"]\n'
            ),
            "main.py": "pass\n",
        }
    )
    assert ev.deps.declared
    assert ev.deps.python_version == ">=3.10"
    names = {d.name for d in ev.deps.dependencies}
    assert names == {"torch", "numpy"}


def test_conda_environment_parsing(make_evidence):
    ev = make_evidence(
        {
            "environment.yml": (
                "name: demo\n"
                "dependencies:\n"
                "  - python=3.11\n"
                "  - numpy=1.26.0\n"
                "  - pandas\n"
            ),
            "main.py": "pass\n",
        }
    )
    assert ev.deps.python_version == "3.11"
    deps = {d.name: d.pin for d in ev.deps.dependencies}
    assert deps["numpy"] is PinLevel.EXACT
    assert deps["pandas"] is PinLevel.UNBOUNDED


def test_python_version_from_dockerfile(make_evidence):
    ev = make_evidence(
        {
            "Dockerfile": "FROM python:3.11-slim\nCOPY . .\n",
            "main.py": "pass\n",
        }
    )
    assert ev.deps.python_version is not None
    assert ev.deps.python_version.startswith("3.11")


def test_dev_requirements_not_counted(make_evidence):
    ev = make_evidence(
        {
            "requirements-dev.txt": "pytest\n",
            "requirements.txt": "torch==2.1.0\n",
            "main.py": "pass\n",
        }
    )
    assert all(d.source == "requirements.txt" for d in ev.deps.dependencies)


def test_pinned_git_dependency(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "git+https://github.com/example/lib@0a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d\n",
            "main.py": "pass\n",
        }
    )
    assert ev.deps.dependencies[0].pin is PinLevel.EXACT
