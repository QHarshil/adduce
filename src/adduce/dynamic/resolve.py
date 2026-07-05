"""Opt-in online resolution of public remote metadata.

Only ever called by ``adduce pin-remotes`` / ``--online``. Queries the
public, unauthenticated Hugging Face and GitHub APIs and URL headers from
the user's machine; results are cached in ``.adduce/cache``. Nothing from
the repository is transmitted — only the artifact identifiers being resolved.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..cache import Cache

_TIMEOUT_SECONDS = 15
_USER_AGENT = "adduce (reproducibility auditor; +https://github.com/QHarshil/adduce)"


@dataclass(frozen=True)
class Resolution:
    identifier: str
    kind: str            # hf-model | hf-dataset | github | url
    sha: str | None      # resolved immutable revision, when the kind has one
    ok: bool
    detail: str          # error or extra metadata (etag, size)


def _get(url: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return response.status, dict(response.headers), response.read()


def _head(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return response.status, dict(response.headers)


def resolve_hf(identifier: str, cache: Cache, dataset: bool = False) -> Resolution:
    kind = "hf-dataset" if dataset else "hf-model"
    cache_key = f"{kind}:{identifier}"
    if (cached := cache.get(cache_key)) is not None:
        return Resolution(identifier, kind, cached.get("sha"), cached.get("ok", False), cached.get("detail", "cached"))
    api = "datasets/" if dataset else "models/"
    url = f"https://huggingface.co/api/{api}{identifier}"
    try:
        status, _, body = _get(url)
        data = json.loads(body)
        sha = data.get("sha")
        resolution = Resolution(identifier, kind, sha, ok=bool(sha), detail=f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        detail = "gated or private" if exc.code in (401, 403) else f"HTTP {exc.code}"
        resolution = Resolution(identifier, kind, None, ok=False, detail=detail)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        resolution = Resolution(identifier, kind, None, ok=False, detail=str(exc)[:120])
    cache.put(cache_key, {"sha": resolution.sha, "ok": resolution.ok, "detail": resolution.detail})
    return resolution


def resolve_github(repo_spec: str, cache: Cache) -> Resolution:
    """Resolve owner/repo[:ref] to the commit SHA of the ref (default branch when omitted)."""
    cache_key = f"github:{repo_spec}"
    if (cached := cache.get(cache_key)) is not None:
        return Resolution(repo_spec, "github", cached.get("sha"), cached.get("ok", False), cached.get("detail", "cached"))
    owner_repo, _, ref = repo_spec.partition(":")
    url = f"https://api.github.com/repos/{owner_repo}/commits/{ref or 'HEAD'}"
    try:
        status, _, body = _get(url)
        data = json.loads(body)
        sha = data.get("sha")
        resolution = Resolution(repo_spec, "github", sha, ok=bool(sha), detail=f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        resolution = Resolution(repo_spec, "github", None, ok=False, detail=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        resolution = Resolution(repo_spec, "github", None, ok=False, detail=str(exc)[:120])
    cache.put(cache_key, {"sha": resolution.sha, "ok": resolution.ok, "detail": resolution.detail})
    return resolution


def resolve_url(url: str, cache: Cache) -> Resolution:
    cache_key = f"url:{url}"
    if (cached := cache.get(cache_key)) is not None:
        return Resolution(url, "url", None, cached.get("ok", False), cached.get("detail", "cached"))
    try:
        status, headers = _head(url)
        etag = headers.get("ETag", "")
        length = headers.get("Content-Length", "?")
        resolution = Resolution(url, "url", None, ok=status < 400, detail=f"HTTP {status}, {length} bytes{', etag ' + etag if etag else ''}")
    except urllib.error.HTTPError as exc:
        resolution = Resolution(url, "url", None, ok=False, detail=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        resolution = Resolution(url, "url", None, ok=False, detail=str(exc)[:120])
    cache.put(cache_key, {"ok": resolution.ok, "detail": resolution.detail})
    return resolution
