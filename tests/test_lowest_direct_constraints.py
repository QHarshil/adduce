"""The lowest-direct constraints generator, which decides what the floor job tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lowest_direct_constraints import constraints_for  # noqa: E402


def test_each_requirement_is_pinned_to_its_declared_floor():
    assert constraints_for(["typer>=0.16.0", "rich>=13.0"]) == [
        "typer==0.16.0",
        "rich==13.0",
    ]


def test_an_environment_marker_survives_the_pin():
    """The marker decides whether the dependency applies at all, so losing it
    would install a package on interpreters that never declared it."""
    assert constraints_for(["tomli>=2.0; python_version < '3.11'"]) == [
        "tomli==2.0; python_version < '3.11'"
    ]


def test_an_extra_is_dropped_from_the_pin_but_the_floor_is_kept():
    assert constraints_for(["uvicorn[standard]>=0.30"]) == ["uvicorn==0.30"]


def test_an_upper_bound_alongside_the_floor_does_not_confuse_the_pin():
    assert constraints_for(["jsonschema>=4.23,<5"]) == ["jsonschema==4.23"]


def test_a_requirement_with_no_lower_bound_is_refused():
    """Dropping it would leave the resolver free to pick the newest release of
    that one package while the job still reported success, so the job would
    silently stop testing the floor for it."""
    with pytest.raises(SystemExit) as excinfo:
        constraints_for(["typer>=0.16.0", "somepkg"])
    assert "somepkg" in str(excinfo.value)


def test_an_exact_pin_is_also_refused_because_it_declares_no_floor():
    with pytest.raises(SystemExit):
        constraints_for(["somepkg==1.2.3"])


def test_every_declared_dependency_of_this_project_can_be_pinned():
    """The script is only useful if it covers what this project actually
    declares; a new dependency without a floor must fail here, not in CI."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on the lowest supported Python
        import tomli as tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = data["project"]["dependencies"]
    pinned = constraints_for(requirements)

    assert len(pinned) == len(requirements)
    assert all("==" in line for line in pinned)
    names = {line.split("==")[0] for line in pinned}
    assert "typer" in names and "libcst" in names
