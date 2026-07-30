"""Guards against a pathological pytest collection node id reaching CI.

A parametrize case once passed a >1 MiB `bytes` value with no explicit
`ids=`, so pytest built a node id of the same size. On Windows that
overflowed the 32,767-char `PYTEST_CURRENT_TEST` env var at setup and
teardown, and the same id was written to the CI log as ~1 MB single lines
that the runner ingested for tens of minutes each -- a 46s job became two
4h49m jobs before anyone noticed, with the local suite green throughout.
The offending case now has explicit short `ids=`; this test stops the next
one. The longest legitimate id in this suite today is 123 chars, so 500 is
headroom, not a target.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_MAX_NODE_ID_LENGTH = 500
_TRUNCATE_AT = 200
_COLLECTION_TIMEOUT_SECONDS = 120.0

_OVERSIZED_ID_FIXTURE = (
    "import pytest\n\n\n"
    '@pytest.mark.parametrize("payload", [b"x" * ((1 << 20) + 1)])\n'
    "def test_accepts_an_oversized_parametrize_value(payload):\n"
    "    assert payload\n"
)


def _collect_node_ids(target: Path, *, timeout: float = _COLLECTION_TIMEOUT_SECONDS) -> list[str]:
    """Real pytest node ids for `target`, collected in a subprocess.

    `pyproject.toml` sets `addopts = "-q"`, so a bare `-q` here would stack
    into pytest 9.1.1's double-quiet mode, which collapses --collect-only
    output to one "<file>: <count>" summary line per module and never
    prints a node id at all -- the exact way the original oversized-id
    defect passed the local suite unnoticed. `-o addopts=` cancels the
    ambient addopts first so the single `-q` added here prints real ids.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", str(target)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, (
        f"collection failed (exit {completed.returncode}):\n"
        f"{completed.stdout[-1000:]}\n{completed.stderr[-1000:]}"
    )
    return [line for line in completed.stdout.splitlines() if "::" in line]


def _assert_node_ids_within_bound(node_ids: list[str], *, bound: int = _MAX_NODE_ID_LENGTH) -> None:
    offender = max(node_ids, key=len)
    assert len(offender) <= bound, (
        f"collected node id is {len(offender)} chars (bound {bound}); "
        f"first {_TRUNCATE_AT} chars: {offender[:_TRUNCATE_AT]!r}...; "
        "a node id this size overflows Windows's 32,767-char "
        "PYTEST_CURRENT_TEST limit and can turn a fast CI job into hours."
    )


def test_real_suite_collects_no_pathologically_long_node_id() -> None:
    node_ids = _collect_node_ids(_TESTS_DIR)
    assert node_ids, "collection produced zero node ids -- see the double-quiet warning above"
    _assert_node_ids_within_bound(node_ids)


def test_guard_actually_fires_on_a_pathological_node_id(tmp_path: Path) -> None:
    """Non-vacuity proof: the guard must be seen failing, not merely present.

    Reproduces the original defect in miniature -- a >1 MiB `bytes`
    parametrize value with no explicit `ids=` -- inside a throwaway
    directory, never in the real suite, and confirms the bound assertion
    actually raises against it.
    """
    fixture_dir = tmp_path / "oversized_id_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "test_oversized_parametrize_id.py").write_text(
        _OVERSIZED_ID_FIXTURE, encoding="utf-8"
    )

    node_ids = _collect_node_ids(fixture_dir)
    assert node_ids
    assert max(len(node_id) for node_id in node_ids) > _MAX_NODE_ID_LENGTH

    with pytest.raises(AssertionError, match="overflows Windows"):
        _assert_node_ids_within_bound(node_ids)
