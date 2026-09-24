"""Dependency manifest parsing and pin-level classification."""

from __future__ import annotations

from pathlib import Path

from adduce.engine import run_check
from adduce.evidence.dependencies import PinLevel
from adduce.rules.base import Status
from adduce.rules.deps import LooseRangeRule, UnpinnedDependencyRule


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


def test_unpinned_dependency_rule_message(make_evidence):
    rule = UnpinnedDependencyRule()

    # Mixed declarations: lower bound, upper bound, bare name, exact pin
    ev = make_evidence(
        {
            "requirements.txt": (
                "apache-tvm-ffi<=0.1.12\n"
                "quack-kernels>=0.3.4\n"
                "torch\n"
                "numpy==1.26.0\n"
            ),
            "main.py": "pass\n",
        }
    )
    finding = rule.evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "admit an unbounded range of future versions" in finding.message
    assert "apache-tvm-ffi<=0.1.12" in finding.message
    assert "quack-kernels>=0.3.4" in finding.message
    assert "torch" in finding.message
    assert "numpy" not in finding.message

    # Fully bounded / exact declarations pass
    ev_pass = make_evidence(
        {
            "requirements.txt": (
                "numpy==1.26.0\n"
                "pandas>=2.0,<3.0\n"
            ),
            "main.py": "pass\n",
        }
    )
    finding_pass = rule.evaluate(ev_pass)
    assert finding_pass.status is Status.PASS


def test_loose_range_rule_message(make_evidence):
    rule = LooseRangeRule()

    # Numerics-bearing libraries with bare name, range, and exact pin, plus non-numeric lib
    ev = make_evidence(
        {
            "requirements.txt": (
                "torch\n"
                "scipy>=1.0\n"
                "numpy==1.26.0\n"
                "requests>=2.0\n"
            ),
            "main.py": "pass\n",
        }
    )
    finding = rule.evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "Result-affecting libraries are not pinned exactly" in finding.message
    assert "torch" in finding.message
    assert "scipy>=1.0" in finding.message
    # requests is not in _NUMERIC_DISTS and numpy is exact
    assert "requests" not in finding.message
    assert "numpy" not in finding.message

    # Exact pins on numerics-bearing libraries pass
    ev_pass = make_evidence(
        {
            "requirements.txt": (
                "torch==2.1.0\n"
                "numpy==1.26.0\n"
                "requests>=2.0\n"
            ),
            "main.py": "pass\n",
        }
    )
    finding_pass = rule.evaluate(ev_pass)
    assert finding_pass.status is Status.PASS


def test_synthetic_dep_ranges_regression_case():
    synthetic_dir = Path(__file__).resolve().parent.parent / "corpus" / "synthetic" / "synthetic_dep_ranges"
    result = run_check(synthetic_dir)
    findings = {f.rule_id: f for f in result.card.findings}

    r_dep_001 = findings["R-DEP-001"]
    assert r_dep_001.status is Status.PARTIAL
    assert "admit an unbounded range of future versions" in r_dep_001.message
    assert "requests<=2.32.0" in r_dep_001.message
    assert "scipy>=1.10.0" in r_dep_001.message
    assert "torch" in r_dep_001.message
    assert "numpy" not in r_dep_001.message

    r_dep_002 = findings["R-DEP-002"]
    assert r_dep_002.status is Status.PARTIAL
    assert "Result-affecting libraries are not pinned exactly" in r_dep_002.message
    assert "scipy>=1.10.0" in r_dep_002.message
    assert "torch" in r_dep_002.message
    assert "requests" not in r_dep_002.message
    assert "numpy" not in r_dep_002.message
