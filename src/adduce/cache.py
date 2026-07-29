"""Local resolution records under ``.adduce/cache``.

Nothing in the default offline path reads or writes these records. Opt-in
online commands store resolved public metadata here so the result remains
inspectable. A pre-existing entry is never accepted as network evidence:
repository contents are untrusted, and an artifact could otherwise forge a
successful resolution. Only entries written by the current ``Cache`` instance
can be read back.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

CACHE_DIR = ".adduce/cache"
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_CACHE_SCHEMA_VERSION = 1
_MAX_CACHE_ENTRY_BYTES = 1 << 20


class Cache:
    def __init__(self, root: Path, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.directory = root / CACHE_DIR
        self.ttl_seconds = ttl_seconds
        self._session_keys: set[str] = set()

    def _path_for(self, key: str) -> Path:
        # A readable prefix helps local inspection, while the digest makes the
        # mapping collision-resistant (for example ``a:b`` and ``a/b`` must
        # never share a cache entry).
        namespace = key.partition(":")[0]
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in namespace)
        prefix = safe.strip("._")[:48] or "entry"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{prefix}-{digest}.json"

    def _ensure_safe_directory(self, *, create: bool) -> bool:
        parent = self.directory.parent
        for path in (parent, self.directory):
            if path.is_symlink():
                raise OSError(f"refusing symlinked cache path: {path}")
            if path.exists() and not path.is_dir():
                raise OSError(f"cache path is not a directory: {path}")
        if create:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.mkdir(mode=0o700, exist_ok=True)
        return self.directory.is_dir()

    def get(self, key: str) -> Any | None:
        # The repository being audited controls every pre-existing file below
        # its root. Treating those bytes as proof of a network response would
        # allow a crafted artifact to turn ``--online`` into a false PASS.
        if key not in self._session_keys:
            return None
        try:
            if not self._ensure_safe_directory(create=False):
                return None
        except OSError:
            return None
        target = self._path_for(key)
        try:
            metadata = target.lstat()
        except OSError:
            return None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_CACHE_ENTRY_BYTES
        ):
            return None
        try:
            entry = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            return None
        if not isinstance(entry, dict):
            return None
        if entry.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None
        expected_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if entry.get("key_sha256") != expected_digest:
            return None
        stored_at = entry.get("stored_at")
        if not isinstance(stored_at, (int, float)) or isinstance(stored_at, bool):
            return None
        age = time.time() - stored_at
        if age < -300 or age > self.ttl_seconds:
            return None
        return entry.get("value")

    def put(self, key: str, value: Any) -> None:
        key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        payload = json.dumps(
            {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "key_sha256": key_digest,
                "stored_at": time.time(),
                "value": value,
            },
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        if len(payload.encode("utf-8")) > _MAX_CACHE_ENTRY_BYTES:
            raise ValueError(f"cache entry exceeds {_MAX_CACHE_ENTRY_BYTES} bytes")
        self._ensure_safe_directory(create=True)
        target = self._path_for(key)
        if target.is_symlink():
            raise OSError(f"refusing symlinked cache entry: {target}")
        if target.exists() and not target.is_file():
            raise OSError(f"cache entry is not a regular file: {target}")
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.directory,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).replace(target)
            self._session_keys.add(key)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
