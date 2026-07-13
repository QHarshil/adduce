#!/usr/bin/env python3
"""Validate a completed corpus run before sampling, reporting, or import."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__:
    from .run_contract import RunContractError, validate_run
else:
    from run_contract import RunContractError, validate_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        metadata = validate_run(args.run)
    except RunContractError as exc:
        print(f"invalid corpus run: {exc}", file=sys.stderr)
        return 1
    print(
        f"valid corpus run: {metadata['n_repositories']} repositories, "
        f"{metadata['n_succeeded']} succeeded, {metadata['n_crashed']} crashed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
