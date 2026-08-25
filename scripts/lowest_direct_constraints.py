"""Emit a pip constraints file pinning every direct dependency to its floor.

The compatibility matrix installs whatever the resolver picks, which in practice
is the newest release of everything. That tests the ceiling and never the floor,
so a lower bound in ``pyproject.toml`` can be wrong for months without anything
noticing: the code starts using an API the declared minimum does not have, and
the only person who finds out is whoever installs into an older environment.

This reads the declared dependencies and pins each ``>=`` bound to exactly that
version, so the lowest-direct job installs the oldest combination the project
claims to support.

A dependency with no ``>=`` bound is an error rather than a skip. Silently
omitting it would leave the resolver free to pick the newest release of that one
package, and the job would still pass while testing something other than the
floor.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on the lowest supported Python
    import tomli as tomllib

#: ``name[extra] >= version`` up to the first marker or comma.
_FLOOR = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*>=\s*(?P<version>[^,;\s]+)"
)


def constraints_for(requirements: list[str]) -> list[str]:
    """Pin each requirement to its declared floor, preserving any marker."""
    pinned: list[str] = []
    unbounded: list[str] = []
    for requirement in requirements:
        match = _FLOOR.match(requirement)
        if match is None:
            unbounded.append(requirement)
            continue
        marker = ""
        if ";" in requirement:
            marker = ";" + requirement.split(";", 1)[1]
        pinned.append(f"{match['name']}=={match['version']}{marker}")

    if unbounded:
        listed = ", ".join(repr(item) for item in unbounded)
        raise SystemExit(
            f"no '>=' lower bound declared for: {listed}. "
            "Give it a floor in pyproject.toml, or this job silently stops "
            "testing the floor for that package."
        )
    return pinned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--output", type=Path, help="write here instead of stdout")
    args = parser.parse_args()

    data = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    requirements = data["project"]["dependencies"]
    if not requirements:
        raise SystemExit("pyproject.toml declares no dependencies to pin")

    rendered = "\n".join(constraints_for(requirements)) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
