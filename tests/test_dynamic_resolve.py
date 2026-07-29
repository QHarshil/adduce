from __future__ import annotations

import threading
import time

import pytest

from adduce.cache import Cache
from adduce.dynamic import resolve
from adduce.evidence.remote import RemoteRef

PUBLIC_IP = "93.184.216.34"
SHA = "a" * 40


def _public_dns(monkeypatch) -> None:
    def getaddrinfo(host, port, **kwargs):
        assert host in {"example.org", "huggingface.co", "api.github.com"}
        return [(resolve.socket.AF_INET, resolve.socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]

    monkeypatch.setattr(resolve.socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/file",
        "http://example.org/file",
        "https://user:password@example.org/file",
        "https://example.org:444/file",
        "https://localhost/file",
        "https://service.internal/file",
        "https://singlelabel/file",
        "https://127.0.0.1/file",
        "https://10.1.2.3/file",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/file",
        "https://[fc00::1]/file",
        "https://[fe80::1]/file",
        "https://224.0.0.1/file",
        "https://[ff02::1]/file",
        "https://[fec0::1]/file",
        "https://[::ffff:127.0.0.1]/file",
        "https://2130706433/file",
        "https://0x7f000001/file",
        "https://%31%32%37.0.0.1/file",
        "https://example.org\\@127.0.0.1/file",
        "https://example.org/line\nbreak",
    ],
)
def test_destination_policy_rejects_unsafe_urls(url, monkeypatch):
    _public_dns(monkeypatch)

    with pytest.raises(resolve.UnsafeResolutionTarget):
        resolve._validated_destination(url)


def test_destination_accepts_mocked_public_https_and_strips_fragment(monkeypatch):
    _public_dns(monkeypatch)

    destination = resolve._validated_destination(
        "HTTPS://Example.Org/path?q=public#fragment"
    )

    assert destination.url == "https://example.org/path?q=public"
    assert destination.hostname == "example.org"
    assert destination.addresses == (PUBLIC_IP,)
    assert destination.request_target == "/path?q=public"


def test_destination_percent_encodes_unicode_path_and_query(monkeypatch):
    _public_dns(monkeypatch)

    destination = resolve._validated_destination(
        "https://example.org/café?q=naïve"
    )

    assert destination.url == "https://example.org/caf%C3%A9?q=na%C3%AFve"
    assert destination.request_target == "/caf%C3%A9?q=na%C3%AFve"


def test_destination_rejects_invalid_percent_escape_before_dns(monkeypatch):
    monkeypatch.setattr(
        resolve.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("DNS must not run for malformed URL"),
    )

    with pytest.raises(resolve.UnsafeResolutionTarget, match="invalid percent escape"):
        resolve._validated_destination("https://example.org/%zz")


def test_destination_rejects_mixed_public_and_private_dns(monkeypatch):
    monkeypatch.setattr(
        resolve.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (resolve.socket.AF_INET, resolve.socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
            (resolve.socket.AF_INET, resolve.socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443)),
        ],
    )

    with pytest.raises(resolve.UnsafeResolutionTarget, match="not globally reachable"):
        resolve._validated_destination("https://example.org/file")


def test_destination_caps_validated_address_candidates(monkeypatch):
    addresses = [f"93.184.216.{suffix}" for suffix in range(1, 21)]
    monkeypatch.setattr(
        resolve.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (resolve.socket.AF_INET, resolve.socket.SOCK_STREAM, 6, "", (address, 443))
            for address in addresses
        ],
    )

    destination = resolve._validated_destination("https://example.org/file")

    assert destination.addresses == tuple(sorted(addresses)[:16])


class _Socket:
    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.endpoint: tuple[object, ...] | None = None
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, endpoint: tuple[object, ...]) -> None:
        self.endpoint = endpoint

    def getpeername(self) -> tuple[str, int]:
        return self.peer, 443

    def close(self) -> None:
        self.closed = True


def test_pinned_connection_rejects_peer_outside_validated_dns(monkeypatch):
    fake = _Socket("10.0.0.9")
    monkeypatch.setattr(resolve.socket, "socket", lambda *args: fake)
    monkeypatch.setattr(resolve.time, "monotonic", lambda: 1.0)

    with pytest.raises(resolve.UnsafeResolutionTarget, match="connected peer"):
        resolve._connect_pinned((PUBLIC_IP,), 443, 6.0)

    assert fake.endpoint == (PUBLIC_IP, 443)
    assert fake.closed


def test_pinned_connection_accepts_validated_public_peer(monkeypatch):
    fake = _Socket(PUBLIC_IP)
    monkeypatch.setattr(resolve.socket, "socket", lambda *args: fake)
    monkeypatch.setattr(resolve.time, "monotonic", lambda: 1.0)

    assert resolve._connect_pinned((PUBLIC_IP,), 443, 6.0) is fake
    assert fake.timeout == 5.0


def test_pinned_connection_rechecks_deadline_between_addresses(monkeypatch):
    first = _Socket(PUBLIC_IP)
    second = _Socket("93.184.216.35")

    def fail_first(endpoint):
        raise OSError("unreachable")

    first.connect = fail_first
    sockets = iter([first, second])
    moments = iter([1.0, 7.0])
    monkeypatch.setattr(resolve.socket, "socket", lambda *args: next(sockets))
    monkeypatch.setattr(resolve.time, "monotonic", lambda: next(moments))

    with pytest.raises(TimeoutError, match="overall deadline"):
        resolve._connect_pinned((PUBLIC_IP, "93.184.216.35"), 443, 6.0)

    assert first.closed
    assert not second.closed


class _Response:
    def __init__(
        self,
        status: int = 200,
        headers: list[tuple[str, str]] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self._headers = headers or []
        self.body = body
        self.read_limit: int | None = None
        self._offset = 0
        self.closed = False

    def getheaders(self) -> list[tuple[str, str]]:
        return self._headers

    def getheader(self, name: str) -> str | None:
        wanted = name.casefold()
        return next((value for key, value in self._headers if key.casefold() == wanted), None)

    def read1(self, limit: int = -1) -> bytes:
        self.read_limit = limit
        if limit < 0:
            chunk = self.body[self._offset :]
            self._offset = len(self.body)
            return chunk
        chunk = self.body[self._offset : self._offset + limit]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False
        self.sock = None

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def _connection_sequence(
    monkeypatch,
    responses: list[_Response],
) -> list[_Connection]:
    connections = [_Connection(response) for response in responses]
    remaining = iter(connections)
    monkeypatch.setattr(
        resolve,
        "_connection",
        lambda destination, deadline, guard=None: next(remaining),
    )
    return connections


def test_request_revalidates_redirect_and_rejects_private_target(monkeypatch):
    _public_dns(monkeypatch)
    connections = _connection_sequence(
        monkeypatch,
        [_Response(302, [("Location", "https://127.0.0.1/private")])],
    )

    with pytest.raises(resolve.UnsafeResolutionTarget, match="not globally reachable"):
        resolve._request("https://example.org/start", "HEAD")

    assert connections[0].response.closed
    assert connections[0].closed


def test_request_rejects_https_downgrade(monkeypatch):
    _public_dns(monkeypatch)
    _connection_sequence(
        monkeypatch,
        [_Response(302, [("Location", "http://example.org/insecure")])],
    )

    with pytest.raises(resolve.UnsafeResolutionTarget, match="only HTTPS"):
        resolve._request("https://example.org/start", "HEAD")


def test_request_enforces_redirect_limit(monkeypatch):
    _public_dns(monkeypatch)
    _connection_sequence(
        monkeypatch,
        [_Response(302, [("Location", f"/redirect-{index}")]) for index in range(4)],
    )

    with pytest.raises(resolve.UnsafeResolutionTarget, match="redirect limit of 3"):
        resolve._request("https://example.org/start", "HEAD")


def test_request_starts_one_deadline_guard_per_transport_hop(monkeypatch):
    _public_dns(monkeypatch)
    _connection_sequence(
        monkeypatch,
        [_Response(302, [("Location", "/final")]), _Response(200)],
    )
    starts = 0
    original_start = resolve._DeadlineGuard.start

    def start(guard):
        nonlocal starts
        starts += 1
        original_start(guard)

    monkeypatch.setattr(resolve._DeadlineGuard, "start", start)

    assert resolve._request("https://example.org/start", "HEAD")[0] == 200
    assert starts == 2


@pytest.mark.parametrize("status", [300, 304, 305, 306])
def test_request_rejects_unhandled_redirect_statuses(monkeypatch, status):
    _public_dns(monkeypatch)
    _connection_sequence(monkeypatch, [_Response(status)])

    with pytest.raises(resolve._HTTPStatusError) as error:
        resolve._request("https://example.org/start", "HEAD")

    assert error.value.status == status


def test_request_sends_only_fixed_credential_free_headers(monkeypatch):
    _public_dns(monkeypatch)
    connections = _connection_sequence(monkeypatch, [_Response(200)])

    assert resolve._request("https://example.org/file", "HEAD") == (200, {}, b"")

    method, target, headers = connections[0].requests[0]
    assert (method, target) == ("HEAD", "/file")
    assert set(headers) == {"Accept", "Connection", "User-Agent"}
    assert not {"Authorization", "Cookie", "Proxy-Authorization"} & set(headers)


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ([], b"x" * ((1 << 20) + 1)),
        ([("Content-Length", "invalid")], b"x" * ((1 << 20) + 1)),
        ([("Content-Length", str((1 << 20) + 1))], b""),
    ],
)
def test_get_enforces_body_limit_with_or_without_valid_length(monkeypatch, headers, body):
    _public_dns(monkeypatch)
    responses = [_Response(200, headers, body)]
    _connection_sequence(monkeypatch, responses)

    with pytest.raises(resolve.ResponseTooLarge, match="response exceeds"):
        resolve._get("https://example.org/metadata")

    if body:
        assert responses[0]._offset == (1 << 20) + 1


def test_body_reader_rechecks_overall_deadline_between_chunks(monkeypatch):
    response = _Response(200, body=b"x" * (70 << 10))
    connection = _Connection(response)
    moments = iter([1.0, 31.0])
    monkeypatch.setattr(resolve.time, "monotonic", lambda: next(moments))

    with pytest.raises(TimeoutError, match="overall deadline"):
        resolve._bounded_body(response, {}, 30.0, connection)

    assert response._offset == 64 << 10


class _BlockingBodyResponse(_Response):
    def __init__(self, closed_event: threading.Event) -> None:
        super().__init__()
        self.closed_event = closed_event

    def read1(self, limit: int = -1) -> bytes:
        self.closed_event.wait(1.0)
        raise OSError("connection closed at deadline")


class _BlockingPhaseConnection(_Connection):
    def __init__(self, phase: str) -> None:
        super().__init__(_Response())
        self.phase = phase
        self.closed_event = threading.Event()

    def _wait_for_deadline(self) -> None:
        self.closed_event.wait(1.0)
        raise OSError("connection closed at deadline")

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        super().request(method, target, headers=headers)
        if self.phase == "request":
            self._wait_for_deadline()

    def getresponse(self) -> _Response:
        if self.phase == "header":
            self._wait_for_deadline()
        if self.phase == "body":
            return _BlockingBodyResponse(self.closed_event)
        return super().getresponse()

    def close(self) -> None:
        self.closed_event.set()
        super().close()


@pytest.mark.parametrize("phase", ["connection", "request", "header", "body"])
def test_request_deadline_closes_active_transport(monkeypatch, phase):
    _public_dns(monkeypatch)
    connection = _BlockingPhaseConnection(phase)
    monkeypatch.setattr(resolve, "_TOTAL_TIMEOUT_SECONDS", 0.05)

    def connect(destination, deadline, guard=None):
        if phase == "connection":
            assert guard is not None
            guard.attach_connection(connection)
            connection._wait_for_deadline()
        return connection

    monkeypatch.setattr(
        resolve,
        "_connection",
        connect,
    )

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="overall deadline"):
        resolve._request("https://example.org/metadata", "GET")

    assert time.monotonic() - started < 0.5
    assert connection.closed


def test_resolver_validates_json_sha_and_uses_cache(tmp_path, monkeypatch):
    calls = 0

    def get(url: str):
        nonlocal calls
        calls += 1
        return 200, {}, f'{{"sha": "{SHA}"}}'.encode()

    monkeypatch.setattr(resolve, "_get", get)
    cache = Cache(tmp_path)

    first = resolve.resolve_hf("org/model", cache)
    second = resolve.resolve_hf("org/model", cache)

    assert first == resolve.Resolution("org/model", "hf-model", SHA, True, "HTTP 200")
    assert second == first
    assert calls == 1


def test_hf_identifier_is_segment_encoded_and_query_injection_is_rejected(
    tmp_path,
    monkeypatch,
):
    requested: list[str] = []

    def get(url: str):
        requested.append(url)
        return 200, {}, f'{{"sha": "{SHA}"}}'.encode()

    monkeypatch.setattr(resolve, "_get", get)

    encoded = resolve.resolve_hf("org/modèle", Cache(tmp_path / "encoded"))
    rejected = resolve.resolve_hf("org/model?revision=other", Cache(tmp_path / "rejected"))

    assert encoded.ok
    assert requested == ["https://huggingface.co/api/models/org/mod%C3%A8le"]
    assert not rejected.ok
    assert rejected.detail == "invalid Hugging Face identifier"


def test_github_ref_is_one_encoded_segment_and_malformed_spec_is_rejected(
    tmp_path,
    monkeypatch,
):
    requested: list[str] = []

    def get(url: str):
        requested.append(url)
        return 200, {}, f'{{"sha": "{SHA}"}}'.encode()

    monkeypatch.setattr(resolve, "_get", get)

    encoded = resolve.resolve_github("owner/repo:feature/topic", Cache(tmp_path / "encoded"))
    rejected = resolve.resolve_github("owner/repo?other", Cache(tmp_path / "rejected"))

    assert encoded.ok
    assert requested == [
        "https://api.github.com/repos/owner/repo/commits/feature%2Ftopic"
    ]
    assert not rejected.ok
    assert rejected.detail == "invalid GitHub identifier"


def test_cached_success_for_revision_kind_requires_full_sha(tmp_path, monkeypatch):
    cache = Cache(tmp_path)
    cache.put("hf-model:org/model", {"ok": True, "sha": None, "detail": "forged"})
    monkeypatch.setattr(resolve, "_get", lambda url: (200, {}, f'{{"sha": "{SHA}"}}'.encode()))

    outcome = resolve.resolve_hf("org/model", cache)

    assert outcome.sha == SHA
    assert outcome.detail == "HTTP 200"


def test_reference_resolution_is_ordered_deduplicated_and_kind_aware(
    tmp_path, monkeypatch
):
    calls: list[tuple[str, str]] = []

    def hf(identifier, cache, dataset=False):
        calls.append(("dataset" if dataset else "model", identifier))
        return resolve.Resolution(identifier, "hf-model", SHA, True, "ok")

    def github(identifier, cache):
        calls.append(("github", identifier))
        return resolve.Resolution(identifier, "github", SHA, True, "ok")

    def url(identifier, cache):
        calls.append(("url", identifier))
        return resolve.Resolution(identifier, "url", None, True, "ok")

    monkeypatch.setattr(resolve, "resolve_hf", hf)
    monkeypatch.setattr(resolve, "resolve_github", github)
    monkeypatch.setattr(resolve, "resolve_url", url)
    references = [
        RemoteRef("hf", 'AutoModel.from_pretrained("org/model")', "a.py", 1, False, "none"),
        RemoteRef("hf", 'AutoModel.from_pretrained("org/model")', "b.py", 2, False, "none"),
        RemoteRef("hf", 'load_dataset("org/data")', "c.py", 3, False, "none"),
        RemoteRef("torch_hub", 'torch.hub.load("owner/repo:main")', "d.py", 4, False, "none"),
        RemoteRef("url", "https://example.org/file", "e.sh", 5, False, "none"),
        RemoteRef("bucket", "s3://bucket/file", "f.sh", 6, False, "none"),
    ]

    outcomes = resolve.resolve_references(references, Cache(tmp_path))

    assert len(outcomes) == 5
    assert calls == [
        ("model", "org/model"),
        ("dataset", "org/data"),
        ("github", "owner/repo:main"),
        ("url", "https://example.org/file"),
    ]
    assert outcomes[-1] == resolve.Resolution(
        "s3://bucket/file",
        "bucket",
        None,
        False,
        "no supported public-metadata resolver",
        supported=False,
    )


def test_long_raw_url_is_preserved_exactly_for_resolution(
    tmp_path,
    monkeypatch,
    make_evidence,
):
    url = "https://example.org/artifacts/" + ("segment-" * 40) + "?token=value"
    evidence = make_evidence({"download.sh": f"curl '{url}'\n"})
    reference = next(item for item in evidence.remote.references if item.kind == "url")
    seen: list[str] = []

    def resolve_url(identifier, cache):
        seen.append(identifier)
        return resolve.Resolution(identifier, "url", None, True, "ok")

    monkeypatch.setattr(resolve, "resolve_url", resolve_url)

    outcomes = resolve.resolve_references([reference], Cache(tmp_path))

    assert reference.spec == url
    assert seen == [url]
    assert outcomes[0].identifier == url


def test_destination_rejects_url_above_explicit_size_limit(monkeypatch):
    monkeypatch.setattr(
        resolve.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("DNS must not run for an oversized URL"),
    )
    oversized = "https://example.org/" + ("x" * resolve._MAX_URL_BYTES)

    with pytest.raises(resolve.UnsafeResolutionTarget, match="8192-byte limit"):
        resolve._validated_destination(oversized)


def test_model_and_dataset_namespaces_deduplicate_independently(
    tmp_path, monkeypatch
):
    calls: list[tuple[str, str]] = []

    def hf(identifier, cache, dataset=False):
        calls.append(("dataset" if dataset else "model", identifier))
        return resolve.Resolution(identifier, "hf-model", SHA, True, "ok")

    monkeypatch.setattr(resolve, "resolve_hf", hf)
    references = [
        RemoteRef("hf", 'AutoModel.from_pretrained("org/shared")', "a.py", 1, False, "none"),
        RemoteRef("hf", 'load_dataset("org/shared")', "b.py", 2, False, "none"),
        RemoteRef(
            "sentence_transformers",
            'SentenceTransformer("org/shared")',
            "c.py",
            3,
            False,
            "none",
        ),
    ]

    outcomes = resolve.resolve_references(references, Cache(tmp_path))

    assert len(outcomes) == 2
    assert calls == [("model", "org/shared"), ("dataset", "org/shared")]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b"[]", "not a JSON object"),
        (b"not-json", "not valid JSON"),
        (b'{"sha": 123}', "no full commit SHA"),
        (b'{"sha": "abc"}', "no full commit SHA"),
    ],
)
def test_resolver_converts_invalid_metadata_to_stable_failure(
    tmp_path, monkeypatch, body, detail
):
    monkeypatch.setattr(resolve, "_get", lambda url: (200, {}, body))

    outcome = resolve.resolve_github("owner/repository", Cache(tmp_path))

    assert not outcome.ok
    assert outcome.sha is None
    assert detail in outcome.detail


def test_resolve_url_rejects_private_target_before_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(
        resolve,
        "_connection",
        lambda *args, **kwargs: pytest.fail("transport must not be reached"),
    )

    outcome = resolve.resolve_url("https://169.254.169.254/latest", Cache(tmp_path))

    assert not outcome.ok
    assert outcome.detail == "destination is not globally reachable"


def test_repository_supplied_cache_cannot_bypass_destination_policy(
    tmp_path, monkeypatch
):
    url = "https://127.0.0.1/private"
    writer = Cache(tmp_path)
    writer.put(f"url:{url}", {"ok": True, "detail": "forged HTTP 200"})
    monkeypatch.setattr(
        resolve,
        "_connection",
        lambda *args, **kwargs: pytest.fail("transport must not be reached"),
    )

    outcome = resolve.resolve_url(url, Cache(tmp_path))

    assert not outcome.ok
    assert outcome.detail == "destination is not globally reachable"


def test_url_diagnostics_redact_secrets_and_sanitize_headers(tmp_path, monkeypatch):
    assert (
        resolve.display_url("https://user:pass@example.org/file?token=secret#fragment")
        == "https://example.org/<redacted-path>?<redacted>"
    )
    monkeypatch.setattr(
        resolve,
        "_head",
        lambda url: (
            200,
            {
                "content-length": "42",
                "etag": "value\n[red]not markup[/red]\x1b[2J",
            },
        ),
    )

    outcome = resolve.resolve_url("https://example.org/file", Cache(tmp_path))

    assert outcome.ok
    assert outcome.detail == "HTTP 200, 42 bytes, etag value [red]not markup[/red] [2J"


def test_display_text_removes_terminal_controls() -> None:
    assert resolve.safe_display_text("model\x1b[2J\x9b31m\nname") == "model [2J 31m name"


def test_remaining_timeout_rejects_expired_deadline(monkeypatch):
    monkeypatch.setattr(resolve.time, "monotonic", lambda: 100.0)

    with pytest.raises(TimeoutError, match="overall deadline"):
        resolve._remaining_timeout(99.0, 5.0)
