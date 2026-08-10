"""Tests for the development-set fetcher.

Nothing here touches the network. The two behaviours that matter most are
exercised without it: a locked evaluation repository is refused before any
fetch is attempted, and untrusted archive content is refused before it is
written to disk.
"""

from __future__ import annotations

import csv
import io
import json
import tarfile
import time
from pathlib import Path

import pytest
from bench.dev import fetch

PAIR_COLUMNS = [
    "id",
    "repo_url",
    "commit_sha",
    "arxiv_id",
    "arxiv_version",
    "paper_title",
    "framework",
    "license",
    "notes",
]

VALID_ROW = {
    "id": "example",
    "repo_url": "https://github.com/example-org/example-repo",
    "commit_sha": "0" * 40,
    "arxiv_id": "2103.03230",
    "arxiv_version": "3",
    "paper_title": "An Example Paper",
    "framework": "pytorch",
    "license": "MIT",
    "notes": "",
}


def _write_pairs(path: Path, rows: list[dict[str, str]], *, columns: list[str] | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or PAIR_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _manifest(out_dir: Path) -> dict[str, dict[str, object]]:
    data = json.loads((out_dir / fetch.MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return {str(record["id"]): record for record in data["records"]}


def _tar_with(members: list[tuple[tarfile.TarInfo, bytes | None]], path: Path) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for info, payload in members:
            tar.addfile(info, io.BytesIO(payload) if payload is not None else None)


# -- holdout refusal ----------------------------------------------------------


@pytest.mark.parametrize("repo_url", sorted(fetch.HOLDOUT_REPOSITORIES))
def test_every_holdout_repository_is_recognised(repo_url: str) -> None:
    assert fetch.is_holdout_repository(f"https://github.com/{repo_url}")


@pytest.mark.parametrize(
    "variant",
    [
        "https://github.com/princeton-nlp/SimCSE",
        "https://github.com/princeton-nlp/SimCSE.git",
        "https://github.com/princeton-nlp/simcse/",
        "http://github.com/PRINCETON-NLP/SimCSE",
        "git@github.com:princeton-nlp/SimCSE.git",
        "https://github.com/princeton-nlp/SimCSE/tree/main",
    ],
)
def test_holdout_is_recognised_through_url_variants(variant: str) -> None:
    """A URL variant must not be able to smuggle a holdout repository in."""
    assert fetch.is_holdout_repository(variant)


def test_a_free_repository_is_not_holdout() -> None:
    assert not fetch.is_holdout_repository("https://github.com/facebookresearch/barlowtwins")


def test_holdout_row_fails_the_run_without_fetching(tmp_path: Path, monkeypatch) -> None:
    """The refusal happens before any network call and fails the run."""

    def _never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a holdout row must be refused before anything is fetched")

    monkeypatch.setattr(fetch, "fetch_pair", _never)

    pairs = tmp_path / "pairs.csv"
    out = tmp_path / "pairs"
    holdout = dict(VALID_ROW, id="simcse", repo_url="https://github.com/princeton-nlp/SimCSE.git")
    _write_pairs(pairs, [holdout])

    exit_code = fetch.main(["--pairs", str(pairs), "--out", str(out)])

    assert exit_code == 1
    record = _manifest(out)["simcse"]
    assert record["status"] == "refused_holdout"
    assert "holdout" in str(record["error"])


# -- malformed input ----------------------------------------------------------


def test_short_row_is_recorded_rather_than_crashing(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    with pairs.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(",".join(PAIR_COLUMNS) + "\n")
        handle.write("truncated,https://github.com/example-org/example-repo\n")

    rows, invalid = fetch.read_pairs(pairs)

    assert rows == []
    assert len(invalid) == 1
    assert invalid[0]["id"] == "truncated"
    assert invalid[0]["status"] == "invalid_row"


def test_one_bad_row_does_not_hide_the_others(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    _write_pairs(
        pairs,
        [
            dict(VALID_ROW, id="good-one"),
            dict(VALID_ROW, id="bad-sha", commit_sha="not-a-sha"),
            dict(VALID_ROW, id="good-two"),
        ],
    )

    rows, invalid = fetch.read_pairs(pairs)

    assert [row.id for row in rows] == ["good-one", "good-two"]
    assert [record["id"] for record in invalid] == ["bad-sha"]


def test_duplicate_id_is_recorded(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    _write_pairs(pairs, [dict(VALID_ROW), dict(VALID_ROW)])

    rows, invalid = fetch.read_pairs(pairs)

    assert [row.id for row in rows] == ["example"]
    assert "duplicate" in str(invalid[0]["error"])


# -- archive safety -----------------------------------------------------------


def test_extraction_accepts_ordinary_files(tmp_path: Path) -> None:
    tarball = tmp_path / "source.tar.gz"
    info = tarfile.TarInfo("paper/main.tex")
    payload = b"\\documentclass{article}\n"
    info.size = len(payload)
    _tar_with([(info, payload)], tarball)

    written = fetch.extract_source_archive(tarball, tmp_path / "src")

    assert written == len(payload)
    assert (tmp_path / "src" / "paper" / "main.tex").read_bytes() == payload


@pytest.mark.parametrize("name", ["../escape.tex", "nested/../../escape.tex", "/etc/passwd"])
def test_extraction_refuses_paths_outside_the_root(tmp_path: Path, name: str) -> None:
    tarball = tmp_path / "source.tar.gz"
    info = tarfile.TarInfo(name)
    info.size = 0
    _tar_with([(info, b"")], tarball)

    with pytest.raises(fetch.ArchiveSafetyError):
        fetch.extract_source_archive(tarball, tmp_path / "src")


def test_extraction_refuses_a_symlink_member(tmp_path: Path) -> None:
    tarball = tmp_path / "source.tar.gz"
    info = tarfile.TarInfo("link.tex")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    _tar_with([(info, None)], tarball)

    with pytest.raises(fetch.ArchiveSafetyError):
        fetch.extract_source_archive(tarball, tmp_path / "src")


def test_extraction_caps_total_bytes(tmp_path: Path) -> None:
    """A small download must not be able to decompress into an unbounded one."""
    tarball = tmp_path / "source.tar.gz"
    payload = b"x" * 4096
    info = tarfile.TarInfo("big.bin")
    info.size = len(payload)
    _tar_with([(info, payload)], tarball)

    with pytest.raises(fetch.ArchiveSafetyError):
        fetch.extract_source_archive(tarball, tmp_path / "src", max_bytes=1024)


def test_extraction_caps_member_count(tmp_path: Path) -> None:
    tarball = tmp_path / "source.tar.gz"
    members = []
    for index in range(5):
        info = tarfile.TarInfo(f"f{index}.tex")
        info.size = 1
        members.append((info, b"x"))
    _tar_with(members, tarball)

    with pytest.raises(fetch.ArchiveSafetyError):
        fetch.extract_source_archive(tarball, tmp_path / "src", max_members=2)


# -- idempotency --------------------------------------------------------------


def _complete_artifacts(pair_dir: Path) -> Path:
    """Lay down every file ``_artifacts_present`` looks for."""
    (pair_dir / "code" / ".git").mkdir(parents=True, exist_ok=True)
    (pair_dir / "code" / "main.py").write_text("x", encoding="utf-8")
    paper = pair_dir / "paper"
    (paper / "src").mkdir(parents=True, exist_ok=True)
    (paper / "src" / "main.tex").write_text("x", encoding="utf-8")
    (paper / "source.tar.gz").write_bytes(b"x")
    (paper / "paper.pdf").write_bytes(b"%PDF-")
    return pair_dir


def test_a_matching_pin_with_artifacts_present_is_not_refetched(tmp_path: Path) -> None:
    row = fetch.read_pairs(_written(tmp_path))[0][0]
    pair_dir = _complete_artifacts(tmp_path / "pairs" / row.id)

    record = {
        "status": "fetched",
        "repo_url": row.repo_url,
        "requested_sha": row.commit_sha,
        "arxiv_id": row.arxiv_id,
        "requested_arxiv_version": row.arxiv_version,
    }

    assert fetch._already_fetched(row, record, pair_dir)


def test_an_empty_checkout_is_not_a_populated_clone(tmp_path: Path) -> None:
    """A pinned commit can resolve to an empty tree and still clone cleanly."""
    code = tmp_path / "code"
    (code / ".git").mkdir(parents=True)

    assert not fetch.code_checkout_is_populated(code)

    (code / "main.py").write_text("x", encoding="utf-8")
    assert fetch.code_checkout_is_populated(code)


def test_an_empty_checkout_is_not_treated_as_fetched(tmp_path: Path) -> None:
    """Every artifact but the code content is present; the pair is still not done.

    This is the shape upstream actually produced: a repository whose contents
    were removed still has commits, so clone and checkout both succeed.
    """
    pair_dir = _complete_artifacts(tmp_path / "pairs" / "example")
    for entry in (pair_dir / "code").iterdir():
        if entry.name != ".git":
            entry.unlink()

    assert (pair_dir / "code" / ".git").exists()
    assert (pair_dir / "paper" / "paper.pdf").is_file()
    assert not fetch._artifacts_present(pair_dir)


def test_a_moved_pin_is_refetched(tmp_path: Path) -> None:
    """Re-pinning to a new commit must refetch even with the old files on disk."""
    row = fetch.read_pairs(_written(tmp_path))[0][0]
    pair_dir = _complete_artifacts(tmp_path / "pairs" / row.id)
    record = {
        "status": "fetched",
        "repo_url": row.repo_url,
        "requested_sha": "1" * 40,
        "arxiv_id": row.arxiv_id,
        "requested_arxiv_version": row.arxiv_version,
    }

    assert fetch._artifacts_present(pair_dir)
    assert not fetch._already_fetched(row, record, pair_dir)


def test_a_failed_record_is_never_treated_as_fetched(tmp_path: Path) -> None:
    """A previous failure must be retried even though its files are on disk.

    The artifacts are created deliberately: without them ``_artifacts_present``
    would decide the outcome on its own and the status check under test would
    never run.
    """
    row = fetch.read_pairs(_written(tmp_path))[0][0]
    pair_dir = _complete_artifacts(tmp_path / "pairs" / row.id)
    record = {
        "status": "failed",
        "repo_url": row.repo_url,
        "requested_sha": row.commit_sha,
        "arxiv_id": row.arxiv_id,
        "requested_arxiv_version": row.arxiv_version,
    }

    assert fetch._artifacts_present(pair_dir)
    assert not fetch._already_fetched(row, record, pair_dir)


def _written(tmp_path: Path) -> Path:
    pairs = tmp_path / "pairs.csv"
    _write_pairs(pairs, [dict(VALID_ROW)])
    return pairs


# -- arXiv throttle -----------------------------------------------------------


def test_the_throttle_spaces_consecutive_requests(monkeypatch) -> None:
    """arXiv is a shared service; consecutive requests must be spaced."""
    clock = {"now": 1000.0}
    slept: list[float] = []
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    throttle = fetch.ArxivThrottle(minimum_interval_seconds=3.0)
    throttle.wait()
    throttle.wait()

    assert slept and slept[0] == pytest.approx(3.0)
