from __future__ import annotations

import hashlib
import json

import pytest

import adduce.cache as cache_module
from adduce.cache import Cache


def _entry(key: str, stored_at: object, value: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "stored_at": stored_at,
        "value": value,
    }


def test_cache_round_trip_uses_collision_resistant_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module.time, "time", lambda: 1_000.0)
    cache = Cache(tmp_path)

    cache.put("url:https://example.org/a", {"ok": True})

    assert cache.get("url:https://example.org/a") == {"ok": True}
    entries = list(cache.directory.glob("*.json"))
    assert len(entries) == 1
    assert len(entries[0].name) < 255
    assert not list(cache.directory.glob("*.tmp"))


def test_new_cache_instance_does_not_trust_repository_supplied_entry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cache_module.time, "time", lambda: 1_000.0)
    writer = Cache(tmp_path)
    writer.put("github:owner/repository", {"ok": True, "sha": "a" * 40})

    reader = Cache(tmp_path)

    assert reader.get("github:owner/repository") is None


def test_cache_keys_with_same_sanitized_prefix_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module.time, "time", lambda: 1_000.0)
    cache = Cache(tmp_path)

    cache.put("a:b", "colon")
    cache.put("a/b", "slash")

    assert cache.get("a:b") == "colon"
    assert cache.get("a/b") == "slash"
    assert cache._path_for("a:b") != cache._path_for("a/b")


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff",
        json.dumps([]).encode(),
        json.dumps(_entry("key", "yesterday", 1)).encode(),
        json.dumps(_entry("key", True, 1)).encode(),
    ],
)
def test_cache_treats_corrupt_or_malformed_entries_as_misses(tmp_path, payload):
    cache = Cache(tmp_path)
    cache.directory.mkdir(parents=True)
    cache._path_for("key").write_bytes(payload)
    cache._session_keys.add("key")

    assert cache.get("key") is None


def test_cache_expires_old_and_implausibly_future_entries(tmp_path, monkeypatch):
    now = 10_000.0
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    cache = Cache(tmp_path, ttl_seconds=60)
    cache.directory.mkdir(parents=True)

    cache._path_for("old").write_text(
        json.dumps(_entry("old", now - 61, "stale")), encoding="utf-8"
    )
    cache._path_for("future").write_text(
        json.dumps(_entry("future", now + 301, "future")), encoding="utf-8"
    )
    cache._session_keys.update({"old", "future"})

    assert cache.get("old") is None
    assert cache.get("future") is None


def test_failed_serialization_preserves_existing_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module.time, "time", lambda: 1_000.0)
    cache = Cache(tmp_path)
    cache.put("key", {"version": 1})
    original = cache._path_for("key").read_bytes()

    with pytest.raises(TypeError):
        cache.put("key", {"invalid": object()})

    assert cache._path_for("key").read_bytes() == original
    assert cache.get("key") == {"version": 1}


def test_non_finite_value_is_not_written(tmp_path):
    cache = Cache(tmp_path)

    with pytest.raises(ValueError, match="JSON compliant"):
        cache.put("key", {"value": float("nan")})

    assert not cache._path_for("key").exists()


def test_cache_rejects_mismatched_key_binding_and_oversized_entry(tmp_path):
    cache = Cache(tmp_path)
    cache.directory.mkdir(parents=True)
    cache._path_for("expected").write_text(
        json.dumps(_entry("different", 1_000.0, "poisoned")), encoding="utf-8"
    )
    cache._path_for("large").write_bytes(b" " * ((1 << 20) + 1))
    cache._session_keys.update({"expected", "large"})

    assert cache.get("expected") is None
    assert cache.get("large") is None


def test_cache_refuses_symlinked_directory_and_entry(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".adduce").symlink_to(outside, target_is_directory=True)
    cache = Cache(tmp_path)

    with pytest.raises(OSError, match="symlinked cache path"):
        cache.put("key", "value")
    assert list(outside.iterdir()) == []

    (tmp_path / ".adduce").unlink()
    cache.directory.mkdir(parents=True)
    external = outside / "external.json"
    external.write_text("unchanged", encoding="utf-8")
    cache._path_for("key").symlink_to(external)
    cache._session_keys.add("key")

    assert cache.get("key") is None
    with pytest.raises(OSError, match="symlinked cache entry"):
        cache.put("key", "value")
    assert external.read_text(encoding="utf-8") == "unchanged"
