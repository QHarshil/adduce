"""No-follow text-file writes for generated Adduce artifacts.

Repository paths are untrusted input.  Generation code must therefore inspect
directory entries with ``lstat``, refuse symbolic links and non-regular files,
and create new artifacts exclusively.  Updating a file that Adduce owns uses a
same-directory temporary file and only replaces the regular file that was
inspected.
"""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


class SafeWriteError(ValueError):
    """A generated artifact could not be written without crossing its boundary."""


@dataclass(frozen=True)
class RegularTextSnapshot:
    """Exact content and stable metadata for an inspected regular text file."""

    payload: bytes
    text: str
    device: int
    inode: int
    size: int
    mode: int
    links: int
    modified_ns: int
    changed_ns: int

    def matches(self, metadata: os.stat_result) -> bool:
        return (
            self.device == metadata.st_dev
            and self.inode == metadata.st_ino
            and self.size == metadata.st_size
            and self.mode == stat.S_IMODE(metadata.st_mode)
            and self.links == metadata.st_nlink
            and self.modified_ns == metadata.st_mtime_ns
            and self.changed_ns == metadata.st_ctime_ns
        )


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _lstat(path: Path, label: str) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SafeWriteError(f"could not inspect {label}") from exc


def _require_directory_ancestors(directory: Path, label: str) -> None:
    """Reject a symlink or non-directory in any lexical parent component."""
    absolute = Path(os.path.abspath(directory))
    for ancestor in reversed(absolute.parents):
        metadata = _lstat(ancestor, f"ancestor of {label}")
        if metadata is None:
            raise SafeWriteError(f"missing ancestor of {label}")
        if stat.S_ISLNK(metadata.st_mode):
            raise SafeWriteError(f"refusing symbolic-link ancestor of {label}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise SafeWriteError(f"refusing non-directory ancestor of {label}")


def ensure_safe_directory(
    directory: Path,
    *,
    label: str,
    create: bool = False,
) -> bool:
    """Require a real directory with no symbolic link in its path."""
    _require_directory_ancestors(directory, label)
    metadata = _lstat(directory, label)
    if metadata is None and create:
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise SafeWriteError(f"could not create {label}") from exc
        metadata = _lstat(directory, label)
    if metadata is None:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        raise SafeWriteError(f"refusing symbolic-link {label}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SafeWriteError(f"refusing non-directory {label}")
    return True


def ensure_safe_directory_tree(directory: Path, *, label: str) -> None:
    """Create missing directory components without traversing symbolic links."""
    absolute = Path(os.path.abspath(directory))
    missing: list[Path] = []
    current = absolute
    while _lstat(current, label) is None:
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise SafeWriteError(f"could not locate an existing ancestor of {label}")
        current = parent
    ensure_safe_directory(current, label=label)
    for component in reversed(missing):
        ensure_safe_directory(component, label=label, create=True)


def regular_file_exists(path: Path, *, label: str) -> bool:
    """Return whether ``path`` is a regular file, rejecting other entries."""
    metadata = _lstat(path, label)
    if metadata is None:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        raise SafeWriteError(f"refusing symbolic-link {label}")
    if not stat.S_ISREG(metadata.st_mode):
        raise SafeWriteError(f"refusing non-regular {label}")
    return True


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("file write made no progress")
        view = view[written:]


def _unlink_if_same(path: Path, metadata: os.stat_result) -> None:
    try:
        current = path.lstat()
    except OSError:
        return
    if _same_file(current, metadata):
        with suppress(OSError):
            path.unlink()


def _scanner_text(payload: bytes) -> str:
    """Mirror ``Path.read_text(..., errors='replace')`` newline handling."""
    return payload.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def snapshot_text_regular(
    path: Path,
    *,
    label: str,
    parent_label: str,
) -> RegularTextSnapshot | None:
    """Capture exact bytes and stable metadata without following links."""
    if not ensure_safe_directory(path.parent, label=parent_label):
        return None
    before = _lstat(path, label)
    if before is None:
        return None
    if stat.S_ISLNK(before.st_mode):
        raise SafeWriteError(f"refusing symbolic-link {label}")
    if not stat.S_ISREG(before.st_mode):
        raise SafeWriteError(f"refusing non-regular {label}")
    if before.st_nlink != 1:
        raise SafeWriteError(f"refusing multiply-linked {label}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SafeWriteError(f"could not open {label}") from exc
    try:
        opened = os.fstat(descriptor)
        current = _lstat(path, label)
        if (
            current is None
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not _same_file(before, opened)
            or not _same_file(opened, current)
            or before.st_size != opened.st_size
            or before.st_mtime_ns != opened.st_mtime_ns
            or before.st_ctime_ns != opened.st_ctime_ns
            or opened.st_nlink != 1
        ):
            raise SafeWriteError(f"refusing changed {label}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read()
            after_opened = os.fstat(handle.fileno())
        after = _lstat(path, label)
        if (
            after is None
            or not _same_file(opened, after_opened)
            or not _same_file(after_opened, after)
            or opened.st_size != after_opened.st_size
            or opened.st_mtime_ns != after_opened.st_mtime_ns
            or opened.st_ctime_ns != after_opened.st_ctime_ns
            or after_opened.st_nlink != 1
            or len(payload) != after_opened.st_size
        ):
            raise SafeWriteError(f"refusing changed {label}")
        return RegularTextSnapshot(
            payload=payload,
            text=_scanner_text(payload),
            device=after_opened.st_dev,
            inode=after_opened.st_ino,
            size=after_opened.st_size,
            mode=stat.S_IMODE(after_opened.st_mode),
            links=after_opened.st_nlink,
            modified_ns=after_opened.st_mtime_ns,
            changed_ns=after_opened.st_ctime_ns,
        )
    except OSError as exc:
        raise SafeWriteError(f"could not read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def create_text_exclusive(
    path: Path,
    content: str,
    *,
    label: str,
    mode: int = 0o666,
    exact_mode: int | None = None,
) -> None:
    """Create one UTF-8 artifact without following or replacing any entry."""
    try:
        payload = content.encode("utf-8")
    except UnicodeError as exc:
        raise SafeWriteError(f"could not encode {label}") from exc
    ensure_safe_directory(path.parent, label=f"{label} parent directory")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError as exc:
        metadata = _lstat(path, label)
        if metadata is not None and stat.S_ISLNK(metadata.st_mode):
            raise SafeWriteError(f"refusing symbolic-link {label}") from exc
        raise SafeWriteError(f"refusing to overwrite existing {label}") from exc
    except OSError as exc:
        raise SafeWriteError(f"could not create {label}") from exc

    created = os.fstat(descriptor)
    complete = False
    try:
        _write_all(descriptor, payload)
        if exact_mode is not None and hasattr(os, "fchmod"):
            os.fchmod(descriptor, exact_mode)
        os.fsync(descriptor)
        complete = os.fstat(descriptor).st_size == len(payload)
        if not complete:
            raise OSError("generated artifact size does not match its payload")
    except OSError as exc:
        raise SafeWriteError(f"could not write {label}") from exc
    finally:
        os.close(descriptor)
        if not complete:
            # Remove only the inode created by this call, never a replacement
            # entry that appeared while the write was in progress.
            _unlink_if_same(path, created)


def append_text_regular(path: Path, content: str, *, label: str) -> None:
    """Atomically append UTF-8 text to the regular file that was inspected."""
    try:
        payload = content.encode("utf-8")
    except UnicodeError as exc:
        raise SafeWriteError(f"could not encode {label}") from exc
    parent_label = f"{label} parent directory"
    original = snapshot_text_regular(
        path,
        label=label,
        parent_label=parent_label,
    )
    if original is None:
        raise SafeWriteError(f"could not append missing {label}")

    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise SafeWriteError(f"could not create temporary {label}") from exc
    created = os.fstat(descriptor)
    complete = False
    try:
        combined = original.payload + payload
        _write_all(descriptor, combined)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, original.mode)
        os.fsync(descriptor)
        complete = os.fstat(descriptor).st_size == len(combined)
        if not complete:
            raise OSError("generated artifact size does not match its payload")
    except OSError as exc:
        raise SafeWriteError(f"could not write temporary {label}") from exc
    finally:
        os.close(descriptor)
        if not complete:
            _unlink_if_same(temporary, created)

    try:
        current = snapshot_text_regular(
            path,
            label=label,
            parent_label=parent_label,
        )
        if current != original:
            raise SafeWriteError(f"refusing changed {label}")
        try:
            os.replace(temporary, path)
        except OSError as exc:
            raise SafeWriteError(f"could not replace {label}") from exc
    finally:
        with suppress(OSError):
            temporary.unlink()


def replace_text_regular(
    path: Path,
    content: str,
    *,
    label: str,
    parent_label: str,
) -> None:
    """Create a file exclusively or atomically update an inspected regular file."""
    try:
        _payload = content.encode("utf-8")
    except UnicodeError as exc:
        raise SafeWriteError(f"could not encode {label}") from exc
    ensure_safe_directory(path.parent, label=parent_label, create=True)
    original = _lstat(path, label)
    if original is None:
        create_text_exclusive(path, content, label=label)
        return
    if stat.S_ISLNK(original.st_mode):
        raise SafeWriteError(f"refusing symbolic-link {label}")
    if not stat.S_ISREG(original.st_mode):
        raise SafeWriteError(f"refusing non-regular {label}")

    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    create_text_exclusive(
        temporary,
        content,
        label=f"temporary {label}",
        mode=0o600,
        exact_mode=stat.S_IMODE(original.st_mode),
    )
    try:
        current = _lstat(path, label)
        if (
            current is None
            or not stat.S_ISREG(current.st_mode)
            or not _same_file(original, current)
        ):
            raise SafeWriteError(f"refusing changed {label}")
        try:
            os.replace(temporary, path)
        except OSError as exc:
            raise SafeWriteError(f"could not replace {label}") from exc
    finally:
        with suppress(OSError):
            temporary.unlink()


def replace_text_regular_if_unchanged(
    path: Path,
    content: str,
    *,
    expected: RegularTextSnapshot,
    label: str,
    parent_label: str,
) -> None:
    """Atomically replace only the exact regular file previously inspected."""
    try:
        _payload = content.encode("utf-8")
    except UnicodeError as exc:
        raise SafeWriteError(f"could not encode {label}") from exc

    current = snapshot_text_regular(path, label=label, parent_label=parent_label)
    if current != expected:
        raise SafeWriteError(f"refusing changed {label}")

    temporary = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    create_text_exclusive(
        temporary,
        content,
        label=f"temporary {label}",
        mode=0o600,
        exact_mode=expected.mode,
    )
    try:
        current = snapshot_text_regular(path, label=label, parent_label=parent_label)
        if current != expected:
            raise SafeWriteError(f"refusing changed {label}")
        try:
            os.replace(temporary, path)
        except OSError as exc:
            raise SafeWriteError(f"could not replace {label}") from exc
    finally:
        with suppress(OSError):
            temporary.unlink()


def read_text_regular(
    path: Path,
    *,
    label: str,
    parent_label: str,
) -> str | None:
    """Read a UTF-8 regular file without following its final path component."""
    if not ensure_safe_directory(path.parent, label=parent_label):
        return None
    before = _lstat(path, label)
    if before is None:
        return None
    if stat.S_ISLNK(before.st_mode):
        raise SafeWriteError(f"refusing symbolic-link {label}")
    if not stat.S_ISREG(before.st_mode):
        raise SafeWriteError(f"refusing non-regular {label}")
    if before.st_nlink != 1:
        raise SafeWriteError(f"refusing multiply-linked {label}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SafeWriteError(f"could not open {label}") from exc
    try:
        opened = os.fstat(descriptor)
        current = _lstat(path, label)
        if (
            current is None
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or not _same_file(before, opened)
            or not _same_file(opened, current)
            or opened.st_nlink != 1
        ):
            raise SafeWriteError(f"refusing changed {label}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return handle.read()
    except UnicodeError:
        raise
    except OSError as exc:
        raise SafeWriteError(f"could not read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
