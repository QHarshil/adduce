"""Opt-in online resolution of public remote metadata.

Only ever called by ``adduce pin-remotes`` / ``--online``. Queries the
public, unauthenticated Hugging Face and GitHub APIs and URL headers from
the user's machine; results are recorded in ``.adduce/cache``. The detected
repository-derived identifiers are transmitted, including the path and query
of a raw URL; repository files are not uploaded.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..cache import Cache

if TYPE_CHECKING:
    from ..evidence.remote import RemoteRef

_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 10.0
_TOTAL_TIMEOUT_SECONDS = 30.0
_MAX_REDIRECTS = 3
_MAX_DESTINATION_ADDRESSES = 16
_MAX_RESPONSE_BYTES = 1 << 20
_MAX_URL_BYTES = 8 << 10
_ALLOWED_SCHEMES = frozenset({"https"})
_DEFAULT_PORTS = {"https": 443}
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9a-fA-F]{2})")
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home.arpa",
)
_USER_AGENT = "adduce (reproducibility auditor; +https://github.com/QHarshil/adduce)"


@dataclass(frozen=True)
class Resolution:
    identifier: str
    kind: str            # hf-model | hf-dataset | github | url
    sha: str | None      # resolved immutable revision, when the kind has one
    ok: bool
    detail: str          # error or extra metadata (etag, size)
    supported: bool = True


class UnsafeResolutionTarget(ValueError):
    """A repository-supplied URL is outside the public HTTP(S) policy."""


class ResponseTooLarge(ValueError):
    """A metadata response exceeded the bounded resolver body size."""


class InvalidMetadataResponse(ValueError):
    """A remote metadata endpoint returned an invalid response."""


class _HTTPStatusError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP {status}")


class _DeadlineGuard:
    """Close active transport resources when an absolute deadline expires."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._connection: http.client.HTTPSConnection | None = None
        self._socket: socket.socket | None = None
        self._expired = False
        self._finished = False

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._expired

    def start(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self._expire()
            raise TimeoutError("metadata request exceeded its overall deadline")
        timer = threading.Timer(remaining, self._expire)
        timer.daemon = True
        with self._lock:
            if self._finished:
                return
            self._timer = timer
        timer.start()

    def attach_connection(self, connection: http.client.HTTPSConnection) -> None:
        with self._lock:
            expired = self._expired
            if not expired:
                self._connection = connection
        if expired:
            with contextlib.suppress(OSError):
                connection.close()
            raise TimeoutError("metadata request exceeded its overall deadline")

    def attach_socket(self, sock: socket.socket) -> None:
        with self._lock:
            expired = self._expired
            if not expired:
                self._socket = sock
        if expired:
            self._close_socket(sock)
            raise TimeoutError("metadata request exceeded its overall deadline")

    def clear_socket(self, sock: socket.socket) -> None:
        with self._lock:
            if self._socket is sock:
                self._socket = None

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            self._expire()
        if self.expired:
            raise TimeoutError("metadata request exceeded its overall deadline")

    def finish(self) -> None:
        with self._lock:
            self._finished = True
            timer = self._timer
            self._timer = None
            self._connection = None
            self._socket = None
        if timer is not None:
            timer.cancel()

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()

    def _expire(self) -> None:
        with self._lock:
            if self._finished or self._expired:
                return
            self._expired = True
            connection = self._connection
            sock = self._socket
        if sock is not None:
            self._close_socket(sock)
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.close()


@dataclass(frozen=True)
class _Destination:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    request_target: str


def _public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    """Resolve a host and reject any address that is not globally reachable."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            raise UnsafeResolutionTarget("hostname could not be resolved") from exc
        addresses = tuple(
            sorted({str(record[4][0]).split("%", 1)[0] for record in records})
        )
        if not addresses:
            raise UnsafeResolutionTarget("hostname resolved to no addresses") from None
    else:
        addresses = (str(literal),)

    for text in addresses:
        try:
            address = ipaddress.ip_address(text)
        except ValueError as exc:
            raise UnsafeResolutionTarget("hostname resolved to an invalid address") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
            or getattr(address, "is_site_local", False)
        ):
            raise UnsafeResolutionTarget("destination is not globally reachable")
    return addresses[:_MAX_DESTINATION_ADDRESSES]


def _validated_destination(url: str) -> _Destination:
    """Validate and normalize one initial or redirected repository URL."""
    try:
        encoded_url = url.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise UnsafeResolutionTarget("URL is not valid UTF-8") from exc
    if len(encoded_url) > _MAX_URL_BYTES:
        raise UnsafeResolutionTarget(f"URL exceeds {_MAX_URL_BYTES}-byte limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise UnsafeResolutionTarget("URL contains control characters")
    if "\\" in url:
        raise UnsafeResolutionTarget("URL contains a backslash")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeResolutionTarget("malformed URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeResolutionTarget("only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeResolutionTarget("embedded URL credentials are not allowed")
    if not parsed.hostname:
        raise UnsafeResolutionTarget("URL has no hostname")

    hostname = parsed.hostname.rstrip(".").lower()
    if "%" in hostname:
        raise UnsafeResolutionTarget("percent-encoded or zone-scoped hostnames are not allowed")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeResolutionTarget("invalid hostname") from exc
    if not ascii_hostname:
        raise UnsafeResolutionTarget("URL has no hostname")

    try:
        literal = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        if "." not in ascii_hostname:
            raise UnsafeResolutionTarget(
                "single-label hostnames are not public destinations"
            ) from None
        if ascii_hostname == "localhost" or ascii_hostname.endswith(_BLOCKED_HOST_SUFFIXES):
            raise UnsafeResolutionTarget("local hostnames are not allowed") from None
        display_hostname = ascii_hostname
    else:
        display_hostname = f"[{literal.compressed}]" if literal.version == 6 else literal.compressed

    effective_port = port or _DEFAULT_PORTS[scheme]
    if effective_port != _DEFAULT_PORTS[scheme]:
        raise UnsafeResolutionTarget("only the default HTTP(S) port is allowed")
    path = parsed.path or "/"
    query = parsed.query
    if _INVALID_PERCENT_ESCAPE_RE.search(path) or _INVALID_PERCENT_ESCAPE_RE.search(query):
        raise UnsafeResolutionTarget("URL contains an invalid percent escape")
    encoded_path = urllib.parse.quote(
        path,
        safe="/%:@!$&'()*+,;=-._~",
        encoding="utf-8",
        errors="strict",
    )
    encoded_query = urllib.parse.quote(
        query,
        safe="/%?:@!$&'()*+,;=-._~",
        encoding="utf-8",
        errors="strict",
    )
    addresses = _public_addresses(ascii_hostname, effective_port)

    netloc = display_hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    normalized = urllib.parse.urlunsplit(
        (scheme, netloc, encoded_path, encoded_query, "")
    )
    if len(normalized.encode("ascii")) > _MAX_URL_BYTES:
        raise UnsafeResolutionTarget(f"encoded URL exceeds {_MAX_URL_BYTES}-byte limit")
    request_target = urllib.parse.urlunsplit(("", "", encoded_path, encoded_query, ""))
    return _Destination(
        url=normalized,
        hostname=ascii_hostname,
        port=effective_port,
        addresses=addresses,
        request_target=request_target,
    )


def _remaining_timeout(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("metadata request exceeded its overall deadline")
    return min(maximum, remaining)


def _connect_pinned(
    addresses: tuple[str, ...],
    port: int,
    deadline: float,
    guard: _DeadlineGuard | None = None,
) -> socket.socket:
    """Connect only to the DNS results that passed the public-address policy."""
    allowed = {ipaddress.ip_address(address) for address in addresses}
    last_error: OSError | None = None
    for address in addresses:
        attempt_timeout = _remaining_timeout(deadline, _CONNECT_TIMEOUT_SECONDS)
        ip = ipaddress.ip_address(address)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        if guard is not None:
            guard.attach_socket(sock)
        try:
            sock.settimeout(attempt_timeout)
            endpoint: tuple[Any, ...]
            endpoint = (address, port, 0, 0) if ip.version == 6 else (address, port)
            sock.connect(endpoint)
            peer = ipaddress.ip_address(sock.getpeername()[0].split("%", 1)[0])
            if (
                peer not in allowed
                or not peer.is_global
                or peer.is_multicast
                or peer.is_unspecified
                or peer.is_reserved
                or getattr(peer, "is_site_local", False)
            ):
                raise UnsafeResolutionTarget("connected peer failed destination validation")
            return sock
        except OSError as exc:
            last_error = exc
            if guard is not None:
                guard.clear_socket(sock)
            sock.close()
        except Exception:
            if guard is not None:
                guard.clear_socket(sock)
            sock.close()
            raise
    raise OSError("could not connect to the validated public destination") from last_error


def _connection(
    destination: _Destination,
    deadline: float,
    guard: _DeadlineGuard | None = None,
) -> http.client.HTTPSConnection:
    connect_timeout = _remaining_timeout(deadline, _CONNECT_TIMEOUT_SECONDS)
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(
        destination.hostname,
        destination.port,
        timeout=connect_timeout,
        context=context,
    )
    if guard is not None:
        guard.attach_connection(connection)
    raw_socket = _connect_pinned(
        destination.addresses,
        destination.port,
        deadline,
        guard,
    )
    try:
        tls_socket = context.wrap_socket(
            raw_socket,
            server_hostname=destination.hostname,
            do_handshake_on_connect=False,
        )
    except Exception:
        if guard is not None:
            guard.clear_socket(raw_socket)
        raw_socket.close()
        raise
    if guard is not None:
        guard.attach_socket(tls_socket)
    connection.sock = tls_socket
    try:
        tls_socket.settimeout(_remaining_timeout(deadline, _CONNECT_TIMEOUT_SECONDS))
        tls_socket.do_handshake()
    except Exception:
        connection.close()
        raise
    tls_socket.settimeout(_remaining_timeout(deadline, _READ_TIMEOUT_SECONDS))
    return connection


def _bounded_body(
    response: http.client.HTTPResponse,
    headers: dict[str, str],
    deadline: float,
    connection: http.client.HTTPSConnection,
) -> bytes:
    raw_length = _header(headers, "Content-Length")
    declared_length: int | None = None
    if raw_length:
        with contextlib.suppress(ValueError):
            declared_length = int(raw_length)
    if declared_length is not None and declared_length > _MAX_RESPONSE_BYTES:
        raise ResponseTooLarge(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
    chunks: list[bytes] = []
    size = 0
    while True:
        read_timeout = _remaining_timeout(deadline, _READ_TIMEOUT_SECONDS)
        response_socket = connection.sock
        if response_socket is None:
            response_file = getattr(response, "fp", None)
            raw_stream = getattr(response_file, "raw", None)
            candidate = getattr(raw_stream, "_sock", None)
            if isinstance(candidate, socket.socket):
                response_socket = candidate
        if response_socket is not None:
            response_socket.settimeout(read_timeout)
        chunk = response.read1(min(64 << 10, _MAX_RESPONSE_BYTES + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")


def _request(url: str, method: str) -> tuple[int, dict[str, str], bytes]:
    current_url = url
    deadline: float | None = None
    for redirect_count in range(_MAX_REDIRECTS + 1):
        destination = _validated_destination(current_url)
        if deadline is None:
            # System DNS is synchronous and cannot be interrupted portably. The
            # enforceable wall-clock budget begins once initial DNS validation
            # has completed and covers every subsequent transport operation.
            deadline = time.monotonic() + _TOTAL_TIMEOUT_SECONDS
        guard = _DeadlineGuard(deadline)
        guard.start()
        connection: http.client.HTTPSConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection = _connection(destination, deadline, guard)
            guard.attach_connection(connection)
            connection.request(
                method,
                destination.request_target,
                headers={
                    "Accept": "application/json",
                    "Connection": "close",
                    "User-Agent": _USER_AGENT,
                },
            )
            guard.check()
            response = connection.getresponse()
            guard.check()
            headers = dict(response.getheaders())
            if response.status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location:
                    raise InvalidMetadataResponse("redirect response has no Location header")
                if redirect_count >= _MAX_REDIRECTS:
                    raise UnsafeResolutionTarget(
                        f"redirect limit of {_MAX_REDIRECTS} exceeded"
                    )
                current_url = urllib.parse.urljoin(destination.url, location)
                continue
            if not 200 <= response.status < 300:
                raise _HTTPStatusError(response.status)
            body = (
                b""
                if method == "HEAD"
                else _bounded_body(response, headers, deadline, connection)
            )
            guard.check()
            return response.status, headers, body
        except Exception as exc:
            if guard.expired or time.monotonic() >= deadline:
                raise TimeoutError("metadata request exceeded its overall deadline") from exc
            raise
        finally:
            guard.finish()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
    raise UnsafeResolutionTarget(f"redirect limit of {_MAX_REDIRECTS} exceeded")


def _get(url: str) -> tuple[int, dict[str, str], bytes]:
    return _request(url, "GET")


def _head(url: str) -> tuple[int, dict[str, str]]:
    status, headers, _ = _request(url, "HEAD")
    return status, headers


def _error_detail(exc: BaseException) -> str:
    if isinstance(
        exc,
        UnsafeResolutionTarget | ResponseTooLarge | InvalidMetadataResponse,
    ):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "metadata request timed out"
    return "metadata request failed"


def _json_sha(body: bytes) -> str:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidMetadataResponse("metadata response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise InvalidMetadataResponse("metadata response is not a JSON object")
    sha = data.get("sha")
    if not isinstance(sha, str) or not _FULL_SHA_RE.fullmatch(sha):
        raise InvalidMetadataResponse("metadata response has no full commit SHA")
    return sha.lower()


def display_url(url: str) -> str:
    """Return a diagnostic URL with credentials, query values, and fragments removed."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return "<invalid URL>"
    hostname = parsed.hostname or "<invalid host>"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname + (f":{port}" if port is not None else "")
    query = "<redacted>" if parsed.query else ""
    path = "/<redacted-path>" if parsed.path not in {"", "/"} else "/"
    return safe_display_text(
        urllib.parse.urlunsplit((parsed.scheme, netloc, path, query, ""))
    )


def safe_display_text(value: str, limit: int | None = None) -> str:
    """Remove terminal controls and normalize whitespace in untrusted diagnostics."""
    printable = "".join(character if character.isprintable() else " " for character in value)
    cleaned = " ".join(printable.split())
    return cleaned[:limit] if limit is not None else cleaned


def _cached_dict(cache: Cache, key: str) -> dict[str, object] | None:
    cached = cache.get(key)
    return cached if isinstance(cached, dict) else None


def _store_cache(cache: Cache, key: str, value: dict[str, object]) -> None:
    # Resolution remains useful when a repository contains a malicious or
    # unwritable cache path. Caching is an optimization, not authority.
    with contextlib.suppress(OSError, TypeError, ValueError):
        cache.put(key, value)


def _resolution_from_cache(
    identifier: str,
    kind: str,
    cached: dict[str, object] | None,
    *,
    with_sha: bool,
) -> Resolution | None:
    if cached is None:
        return None
    ok = cached.get("ok")
    detail = cached.get("detail")
    sha = cached.get("sha") if with_sha else None
    if not isinstance(ok, bool) or not isinstance(detail, str):
        return None
    if with_sha:
        if ok and (not isinstance(sha, str) or not _FULL_SHA_RE.fullmatch(sha)):
            return None
        if not ok and sha is not None:
            return None
    return Resolution(identifier, kind, sha if isinstance(sha, str) else None, ok, detail)


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            return value
    return None


def _safe_header_value(value: str, limit: int = 120) -> str:
    return safe_display_text(value, limit)


def _quoted_repository_identifier(
    identifier: str,
    *,
    service: str,
    minimum_segments: int,
    maximum_segments: int,
) -> str:
    """Validate a repository identifier and encode each API path segment."""
    try:
        encoded = identifier.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise UnsafeResolutionTarget(f"invalid {service} identifier") from exc
    if not encoded or len(encoded) > 512:
        raise UnsafeResolutionTarget(f"invalid {service} identifier")
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in identifier)
        or any(character in identifier for character in "\\?#")
    ):
        raise UnsafeResolutionTarget(f"invalid {service} identifier")
    segments = identifier.split("/")
    if (
        not minimum_segments <= len(segments) <= maximum_segments
        or any(not segment or segment in {".", ".."} for segment in segments)
    ):
        raise UnsafeResolutionTarget(f"invalid {service} identifier")
    return "/".join(
        urllib.parse.quote(segment, safe="-._~", encoding="utf-8", errors="strict")
        for segment in segments
    )


def resolve_hf(identifier: str, cache: Cache, dataset: bool = False) -> Resolution:
    kind = "hf-dataset" if dataset else "hf-model"
    cache_key = f"{kind}:{identifier}"
    cached_resolution = _resolution_from_cache(
        identifier,
        kind,
        _cached_dict(cache, cache_key),
        with_sha=True,
    )
    if cached_resolution is not None:
        return cached_resolution
    try:
        quoted_identifier = _quoted_repository_identifier(
            identifier,
            service="Hugging Face",
            minimum_segments=1,
            maximum_segments=2,
        )
        api = "datasets/" if dataset else "models/"
        url = f"https://huggingface.co/api/{api}{quoted_identifier}"
        status, _, body = _get(url)
        sha = _json_sha(body)
        resolution = Resolution(identifier, kind, sha, ok=True, detail=f"HTTP {status}")
    except _HTTPStatusError as exc:
        detail = "gated or private" if exc.status in (401, 403) else f"HTTP {exc.status}"
        resolution = Resolution(identifier, kind, None, ok=False, detail=detail)
    except (
        TimeoutError,
        OSError,
        http.client.HTTPException,
        UnsafeResolutionTarget,
        ResponseTooLarge,
        InvalidMetadataResponse,
        UnicodeError,
    ) as exc:
        resolution = Resolution(identifier, kind, None, ok=False, detail=_error_detail(exc))
    _store_cache(
        cache,
        cache_key,
        {"sha": resolution.sha, "ok": resolution.ok, "detail": resolution.detail},
    )
    return resolution


def resolve_github(repo_spec: str, cache: Cache) -> Resolution:
    """Resolve owner/repo[:ref] to the commit SHA of the ref (default branch when omitted)."""
    cache_key = f"github:{repo_spec}"
    cached_resolution = _resolution_from_cache(
        repo_spec,
        "github",
        _cached_dict(cache, cache_key),
        with_sha=True,
    )
    if cached_resolution is not None:
        return cached_resolution
    try:
        owner_repo, separator, ref = repo_spec.partition(":")
        quoted_repository = _quoted_repository_identifier(
            owner_repo,
            service="GitHub",
            minimum_segments=2,
            maximum_segments=2,
        )
        if separator and (
            not ref
            or len(ref.encode("utf-8")) > 512
            or any(ord(character) < 32 or ord(character) == 127 for character in ref)
            or any(character in ref for character in "\\?#:")
        ):
            raise UnsafeResolutionTarget("invalid GitHub ref")
        quoted_ref = urllib.parse.quote(
            ref or "HEAD",
            safe="-._~",
            encoding="utf-8",
            errors="strict",
        )
        url = f"https://api.github.com/repos/{quoted_repository}/commits/{quoted_ref}"
        status, _, body = _get(url)
        sha = _json_sha(body)
        resolution = Resolution(repo_spec, "github", sha, ok=True, detail=f"HTTP {status}")
    except _HTTPStatusError as exc:
        resolution = Resolution(repo_spec, "github", None, ok=False, detail=f"HTTP {exc.status}")
    except (
        TimeoutError,
        OSError,
        http.client.HTTPException,
        UnsafeResolutionTarget,
        ResponseTooLarge,
        InvalidMetadataResponse,
        UnicodeError,
    ) as exc:
        resolution = Resolution(repo_spec, "github", None, ok=False, detail=_error_detail(exc))
    _store_cache(
        cache,
        cache_key,
        {"sha": resolution.sha, "ok": resolution.ok, "detail": resolution.detail},
    )
    return resolution


def resolve_url(url: str, cache: Cache) -> Resolution:
    cache_key = f"url:{url}"
    cached_resolution = _resolution_from_cache(
        url,
        "url",
        _cached_dict(cache, cache_key),
        with_sha=False,
    )
    if cached_resolution is not None:
        return cached_resolution
    try:
        status, headers = _head(url)
        etag = _safe_header_value(_header(headers, "ETag") or "")
        length = _safe_header_value(_header(headers, "Content-Length") or "?")
        detail = f"HTTP {status}, {length} bytes"
        if etag:
            detail += f", etag {etag}"
        resolution = Resolution(url, "url", None, ok=True, detail=detail)
    except _HTTPStatusError as exc:
        resolution = Resolution(url, "url", None, ok=False, detail=f"HTTP {exc.status}")
    except (
        TimeoutError,
        OSError,
        http.client.HTTPException,
        UnsafeResolutionTarget,
        ResponseTooLarge,
        InvalidMetadataResponse,
        UnicodeError,
    ) as exc:
        resolution = Resolution(url, "url", None, ok=False, detail=_error_detail(exc))
    _store_cache(cache, cache_key, {"ok": resolution.ok, "detail": resolution.detail})
    return resolution


def resolve_references(references: Iterable[RemoteRef], cache: Cache) -> list[Resolution]:
    """Resolve each supported reference once while preserving deterministic order."""
    outcomes: list[Resolution] = []
    seen: set[tuple[str, str]] = set()
    for reference in references:
        identifier: str | None = None
        resolver_kind = reference.resolver_kind or reference.kind
        if reference.kind in {"hf", "sentence_transformers"}:
            identifier = reference.spec.split('"')[1] if '"' in reference.spec else None
            if reference.resolver_kind is None:
                resolver_kind = (
                    "hf-dataset" if "load_dataset" in reference.spec else "hf-model"
                )
        elif reference.kind == "url":
            identifier = reference.spec
        elif reference.kind == "torch_hub" and '"' in reference.spec:
            identifier = reference.spec.split('"')[1]
            resolver_kind = "github"
        if not identifier:
            unsupported_key = (f"unsupported:{reference.kind}", reference.spec)
            if unsupported_key not in seen:
                seen.add(unsupported_key)
                outcomes.append(
                    Resolution(
                        reference.spec,
                        reference.kind,
                        None,
                        False,
                        "no supported public-metadata resolver",
                        supported=False,
                    )
                )
            continue
        key = (resolver_kind, identifier)
        if key in seen:
            continue
        seen.add(key)
        if resolver_kind in {"hf-model", "hf-dataset"}:
            outcomes.append(
                resolve_hf(
                    identifier,
                    cache,
                    dataset=resolver_kind == "hf-dataset",
                )
            )
        elif resolver_kind == "github":
            outcomes.append(resolve_github(identifier, cache))
        else:
            outcomes.append(resolve_url(identifier, cache))
    return outcomes
