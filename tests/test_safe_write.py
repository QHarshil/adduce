"""Unit tests for bounded fixed-path text I/O."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from adduce import safe_write
from adduce.safe_write import (
    SafeWriteError,
    append_text_regular,
    create_text_exclusive,
    ensure_safe_directory,
    ensure_safe_directory_tree,
    read_text_regular,
    regular_file_exists,
    replace_text_regular,
)


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")


def test_exclusive_create_writes_text_and_refuses_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"

    create_text_exclusive(target, "first\n", label="artifact")

    with pytest.raises(SafeWriteError, match="refusing to overwrite existing artifact"):
        create_text_exclusive(target, "second\n", label="artifact")
    assert target.read_text(encoding="utf-8") == "first\n"


def test_exclusive_create_removes_its_partial_file_on_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.txt"

    def fail_write(_descriptor: int, _payload: bytes) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(safe_write, "_write_all", fail_write)

    with pytest.raises(SafeWriteError, match="could not write artifact"):
        create_text_exclusive(target, "content", label="artifact")
    assert not target.exists()


def test_append_updates_only_a_single_link_regular_file(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text("first\n", encoding="utf-8")

    append_text_regular(target, "second\n", label="README")

    assert target.read_text(encoding="utf-8") == "first\nsecond\n"


def test_append_write_failure_preserves_original_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("first\n", encoding="utf-8")

    def fail_write(descriptor: int, payload: bytes) -> None:
        os.write(descriptor, payload[:2])
        raise OSError("simulated write failure")

    monkeypatch.setattr(safe_write, "_write_all", fail_write)

    with pytest.raises(SafeWriteError, match="could not write temporary README"):
        append_text_regular(target, "second\n", label="README")

    assert target.read_text(encoding="utf-8") == "first\n"
    assert list(tmp_path.glob(".README.md.*.tmp")) == []


def test_append_refuses_hard_link_and_preserves_both_names(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    target = tmp_path / "README.md"
    outside.write_text("preserve\n", encoding="utf-8")
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(SafeWriteError, match="multiply-linked README"):
        append_text_regular(target, "unsafe\n", label="README")

    assert target.read_text(encoding="utf-8") == "preserve\n"
    assert outside.read_text(encoding="utf-8") == "preserve\n"


def test_replace_preserves_regular_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)
    original_mode = stat.S_IMODE(target.stat().st_mode)

    replace_text_regular(
        target,
        "new\n",
        label="artifact",
        parent_label="artifact directory",
    )

    assert target.read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode


def test_replace_failure_preserves_target_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.txt"
    target.write_text("old\n", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(safe_write.os, "replace", fail_replace)

    with pytest.raises(SafeWriteError, match="could not replace artifact"):
        replace_text_regular(
            target,
            "new\n",
            label="artifact",
            parent_label="artifact directory",
        )

    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".artifact.txt.*.tmp")) == []


def test_replace_and_read_refuse_symbolic_link_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve\n", encoding="utf-8")
    target = tmp_path / "artifact.txt"
    _symlink(target, outside)

    with pytest.raises(SafeWriteError, match="symbolic-link artifact"):
        replace_text_regular(
            target,
            "new\n",
            label="artifact",
            parent_label="artifact directory",
        )
    with pytest.raises(SafeWriteError, match="symbolic-link artifact"):
        read_text_regular(
            target,
            label="artifact",
            parent_label="artifact directory",
        )

    assert outside.read_text(encoding="utf-8") == "preserve\n"


def test_replace_refuses_non_regular_target(tmp_path: Path) -> None:
    target = tmp_path / "artifact.txt"
    target.mkdir()

    with pytest.raises(SafeWriteError, match="non-regular artifact"):
        replace_text_regular(
            target,
            "new\n",
            label="artifact",
            parent_label="artifact directory",
        )

    assert target.is_dir()


def test_read_refuses_multiply_linked_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    target = tmp_path / "artifact.txt"
    outside.write_text("private\n", encoding="utf-8")
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(SafeWriteError, match="multiply-linked artifact"):
        read_text_regular(
            target,
            label="artifact",
            parent_label="artifact directory",
        )


def test_directory_checks_reject_symbolic_link_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    nested = outside / "nested"
    nested.mkdir(parents=True)
    link = tmp_path / "linked"
    _symlink(link, outside, directory=True)

    with pytest.raises(
        SafeWriteError,
        match="symbolic-link ancestor of artifact directory",
    ):
        ensure_safe_directory(link / "nested", label="artifact directory")


def test_directory_tree_creation_is_nested_and_no_follow(tmp_path: Path) -> None:
    target = tmp_path / "reports" / "adduce"

    ensure_safe_directory_tree(target, label="output directory")

    assert target.is_dir()

    outside = tmp_path / "outside-tree"
    outside.mkdir()
    linked = tmp_path / "linked-tree"
    _symlink(linked, outside, directory=True)
    with pytest.raises(SafeWriteError, match="symbolic-link"):
        ensure_safe_directory_tree(
            linked / "nested",
            label="output directory",
        )


def test_directory_create_and_regular_file_probe(tmp_path: Path) -> None:
    directory = tmp_path / "generated"
    assert ensure_safe_directory(directory, label="generated directory", create=True)

    target = directory / "artifact.txt"
    assert not regular_file_exists(target, label="artifact")
    target.write_text("content\n", encoding="utf-8")
    assert regular_file_exists(target, label="artifact")

    non_file = directory / "nested"
    non_file.mkdir()
    with pytest.raises(SafeWriteError, match="non-regular artifact"):
        regular_file_exists(non_file, label="artifact")


def test_read_returns_none_for_missing_parent_or_file(tmp_path: Path) -> None:
    assert (
        read_text_regular(
            tmp_path / "missing" / "artifact.txt",
            label="artifact",
            parent_label="artifact directory",
        )
        is None
    )
    assert (
        read_text_regular(
            tmp_path / "artifact.txt",
            label="artifact",
            parent_label="artifact directory",
        )
        is None
    )
