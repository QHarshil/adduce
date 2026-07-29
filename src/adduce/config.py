"""User configuration: adduce.toml or the [tool.adduce] table in pyproject.toml."""

from __future__ import annotations

import contextlib
import math
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib


_MAX_CONFIG_BYTES = 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


@dataclass
class Config:
    profile: str = "default"
    ignore: frozenset[str] = frozenset()
    exclude: tuple[str, ...] = ()
    fail_under: float | None = None
    source: str | None = None
    repository_policy_honored: bool = True


def _invalid(source: str, detail: str) -> ValueError:
    return ValueError(f"invalid {source}: {detail}")


def _read_config(path: Path, source: str) -> str | None:
    """Read one bounded root-level config without following a file symlink."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise _invalid(source, "could not inspect the file safely") from None

    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise _invalid(source, "must be a non-symlink regular file")
    if before.st_size > _MAX_CONFIG_BYTES:
        raise _invalid(source, f"exceeds the {_MAX_CONFIG_BYTES}-byte size limit")

    flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag_name, 0)

    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise _invalid(source, "could not open the file safely") from None

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _invalid(source, "must be a non-symlink regular file")
        if not os.path.samestat(before, opened):
            raise _invalid(source, "changed while it was being opened")
        if opened.st_size > _MAX_CONFIG_BYTES:
            raise _invalid(source, f"exceeds the {_MAX_CONFIG_BYTES}-byte size limit")

        chunks: list[bytes] = []
        remaining = _MAX_CONFIG_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise _invalid(source, "could not read the file safely") from None
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)

    raw = b"".join(chunks)
    if len(raw) > _MAX_CONFIG_BYTES:
        raise _invalid(source, f"exceeds the {_MAX_CONFIG_BYTES}-byte size limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _invalid(source, "must be valid UTF-8") from None


def _load_toml(path: Path, source: str) -> dict[str, object] | None:
    content = _read_config(path, source)
    if content is None:
        return None
    try:
        return cast(dict[str, object], tomllib.loads(content))
    except tomllib.TOMLDecodeError:
        raise _invalid(source, "contains malformed TOML") from None


def _string_list(table: dict[str, object], key: str, source: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _invalid(source, f"'{key}' must be an array of strings")
    return value


def _fail_under(table: dict[str, object], source: str) -> float | None:
    if "fail-under" in table and "fail_under" in table:
        raise _invalid(source, "use only one of 'fail-under' and 'fail_under'")
    key = "fail-under" if "fail-under" in table else "fail_under"
    if key not in table:
        return None
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(source, f"'{key}' must be a number between 0 and 100")
    if (isinstance(value, float) and not math.isfinite(value)) or not 0 <= value <= 100:
        raise _invalid(source, f"'{key}' must be a finite number between 0 and 100")
    return float(value)


def _parse_table(table: dict[str, object], source: str) -> Config:
    profile = table.get("profile", "default")
    if not isinstance(profile, str):
        raise _invalid(source, "'profile' must be a string")
    return Config(
        profile=profile,
        ignore=frozenset(_string_list(table, "ignore", source)),
        exclude=tuple(_string_list(table, "exclude", source)),
        fail_under=_fail_under(table, source),
        source=source,
    )


def load_config(root: Path) -> Config:
    """Read adduce.toml if present, otherwise [tool.adduce] from pyproject.toml."""
    standalone = root / "adduce.toml"
    data = _load_toml(standalone, "adduce.toml")
    if data is not None:
        return _parse_table(data, "adduce.toml")

    pyproject = root / "pyproject.toml"
    data = _load_toml(pyproject, "pyproject.toml")
    if data is None or "tool" not in data:
        return Config()
    tool = data["tool"]
    if not isinstance(tool, dict):
        raise _invalid("pyproject.toml", "'tool' must be a table")
    if "adduce" not in tool:
        return Config()
    table = tool["adduce"]
    if not isinstance(table, dict):
        raise _invalid("pyproject.toml", "'[tool.adduce]' must be a table")
    return _parse_table(table, "pyproject.toml [tool.adduce]")
