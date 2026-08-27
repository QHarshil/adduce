"""Cross-platform stability of the file inventory's order.

``scan_repository`` once sorted the walk's native ``Path`` objects. Both path
flavours compare segment by segment, but each under its own case rules, so a
Windows host casefolded the comparison and ordered the same tree differently
from a POSIX one. Inventory order reaches rendered output, including the
truncated file lists in reports, so one commit audited on two platforms could
order its inventory differently and name different files. The observed case is
the pinned corpus clone ``simcse`` (``princeton-nlp/SimCSE`` at
49fa580a853752ede55b8c76d9debf748e214d3f), where casefolding pulls
``data/download_nli.sh`` ahead of ``LICENSE``.

Sorting the segments of the relative ``PurePosixPath`` fixes this without
moving POSIX order, since ``PurePosixPath`` never casefolds. That matters
because the recorded corpus outputs were produced on POSIX. The digest below
is the cross-platform contract: a Windows leg that computes a different one
has reintroduced the divergence.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath

from adduce.model import scan_repository

#: Names chosen to provoke every divergence at once while remaining creatable
#: on a case-insensitive filesystem, so no two siblings differ only by case:
#: mixed case that casefolding would reorder (``README.md`` before ``data/``,
#: ``scripts/Run.py`` before ``scripts/build.sh``), and a file whose stem is
#: also a sibling directory (``model.py``, ``model/``) so the boundary between
#: segments is observable.
_FIXTURE_NAMES = (
    "zeta.txt",
    "scripts/build.sh",
    "scripts/Run.py",
    "model/layers.py",
    "model.py",
    "data/raw/values.csv",
    "data/Sample.csv",
    "README.md",
    "Makefile",
)

_EXPECTED_ORDER = (
    "Makefile",
    "README.md",
    "data/Sample.csv",
    "data/raw/values.csv",
    # A segment key compares "model" against "model.py", so the subtree sorts
    # ahead of its sibling file. Sorting the joined strings would invert this
    # pair, because "." precedes "/"; that is the rejected key, not a defect.
    "model/layers.py",
    "model.py",
    "scripts/Run.py",
    "scripts/build.sh",
    "zeta.txt",
)

_GOLDEN_DIGEST = "9d31379dd919524f6a3f1dbfefe9557c65044918358a2d48385ad25c4e6b4c74"


def _build_tree(root: Path, names: tuple[str, ...] = _FIXTURE_NAMES) -> Path:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    return root


def _inventory(root: Path) -> list[str]:
    repo = scan_repository(root, honor_gitignore=False)
    return [str(entry.path) for entry in repo.files]


def _digest(paths: list[str]) -> str:
    return hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()


def test_inventory_digest_matches_the_committed_cross_platform_contract(tmp_path: Path) -> None:
    paths = _inventory(_build_tree(tmp_path / "tree"))
    assert paths == list(_EXPECTED_ORDER)
    assert _digest(paths) == _GOLDEN_DIGEST


def test_inventory_is_ordered_by_relative_posix_path_segments(tmp_path: Path) -> None:
    paths = _inventory(_build_tree(tmp_path / "tree"))
    by_segments = sorted(paths, key=lambda text: PurePosixPath(text).parts)
    assert paths == by_segments
    # Not the joined string: that key would invert the model.py pair.
    assert paths != sorted(paths)


def test_inventory_order_is_case_sensitive(tmp_path: Path) -> None:
    paths = _inventory(_build_tree(tmp_path / "tree"))
    # Casefolding is the sole Windows-versus-POSIX driver, so uppercase
    # siblings must keep sorting ahead of lowercase ones.
    assert paths.index("README.md") < paths.index("data/Sample.csv")
    assert paths.index("scripts/Run.py") < paths.index("scripts/build.sh")
    assert paths.index("data/Sample.csv") < paths.index("data/raw/values.csv")
    casefolded = sorted(paths, key=lambda text: tuple(s.casefold() for s in text.split("/")))
    assert paths != casefolded


def test_inventory_order_does_not_follow_native_path_comparison(tmp_path: Path) -> None:
    paths = _inventory(_build_tree(tmp_path / "tree"))
    # What the walk produced on Windows when it sorted native Path objects.
    windows_order = [p.as_posix() for p in sorted(PureWindowsPath(text) for text in paths)]
    assert windows_order != paths


def test_inventory_order_is_independent_of_creation_order(tmp_path: Path) -> None:
    ascending = tuple(sorted(_FIXTURE_NAMES))
    descending = tuple(reversed(ascending))
    forward = _inventory(_build_tree(tmp_path / "forward", ascending))
    reverse = _inventory(_build_tree(tmp_path / "reverse", descending))
    assert forward == reverse == list(_EXPECTED_ORDER)
