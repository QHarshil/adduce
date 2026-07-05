"""Local cache under ``.adduce/cache`` for opt-in online resolutions.

Nothing in the default offline path reads or writes this cache. Online
commands (``pin-remotes``, ``--online``) store resolved public metadata
(Hugging Face revisions, GitHub SHAs, URL headers) here so repeated runs do
not re-query, and so the resolved values are inspectable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = ".adduce/cache"
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600


class Cache:
    def __init__(self, root: Path, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self.directory = root / CACHE_DIR
        self.ttl_seconds = ttl_seconds

    def _path_for(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        return self.directory / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        target = self._path_for(key)
        if not target.is_file():
            return None
        try:
            entry = json.loads(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - entry.get("stored_at", 0) > self.ttl_seconds:
            return None
        return entry.get("value")

    def put(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {"stored_at": time.time(), "value": value}
        self._path_for(key).write_text(json.dumps(entry, indent=2), encoding="utf-8")
