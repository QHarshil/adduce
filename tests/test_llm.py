"""Optional provider integration stays fenced from deterministic answers."""

from __future__ import annotations

import pytest

from adduce import llm


def test_hosted_provider_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("ADDUCE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ADDUCE_LLM_MODEL", raising=False)
    called = False

    def unexpected_post(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(llm, "_post_json", unexpected_post)

    with pytest.raises(llm.LLMUnavailable, match="ADDUCE_LLM_MODEL"):
        llm.complete("summary")
    assert not called


def test_justification_prompt_contains_only_supplied_summary_text(monkeypatch):
    captured: dict[str, str | int] = {}

    def fake_complete(prompt: str, max_tokens: int = 500) -> str:
        captured.update(prompt=prompt, max_tokens=max_tokens)
        return "draft"

    monkeypatch.setattr(llm, "complete", fake_complete)

    result = llm.draft_justification(
        "Are seeds documented?",
        ["partial: torch seed detected; NumPy seed not detected"],
    )

    assert result == "draft"
    assert captured["max_tokens"] == 220
    assert "Are seeds documented?" in str(captured["prompt"])
    assert "torch seed detected; NumPy seed not detected" in str(captured["prompt"])
    assert "may be incomplete or incorrect" in str(captured["prompt"])
    assert "ground truth" not in str(captured["prompt"]).lower()
    assert "Do not convert a detection into a claim that execution occurred" in str(
        captured["prompt"]
    )


def test_provider_identity_records_only_non_secret_configuration(monkeypatch):
    monkeypatch.setenv("ADDUCE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("ADDUCE_LLM_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-recorded")

    identity = llm.provider_identity()

    assert identity.provider == "openai"
    assert identity.model == "configured-model"
    assert "must-not-be-recorded" not in repr(identity)


def test_provider_response_body_is_bounded(monkeypatch):
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size):
            assert size == (1 << 20) + 1
            return b"x" * size

    class Opener:
        def open(self, request, timeout):
            return OversizedResponse()

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *handlers: Opener())

    with pytest.raises(llm.LLMUnavailable, match="1 MiB"):
        llm._post_json("https://provider.invalid", {}, {})


def test_provider_response_requires_utf8_json(monkeypatch):
    class InvalidResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size):
            return b"\xff"

    class Opener:
        def open(self, request, timeout):
            return InvalidResponse()

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *handlers: Opener())

    with pytest.raises(llm.LLMUnavailable):
        llm._post_json("https://provider.invalid", {}, {})


def test_provider_credentials_cannot_follow_cross_origin_redirects(monkeypatch):
    installed_handlers = []

    class RefusingOpener:
        def open(self, request, timeout):
            raise llm.urllib.error.URLError("redirect refused")

    def build_opener(*handlers):
        installed_handlers.extend(handlers)
        return RefusingOpener()

    monkeypatch.setattr(llm.urllib.request, "build_opener", build_opener)

    with pytest.raises(llm.LLMUnavailable, match="redirect refused"):
        llm._post_json(
            "https://api.provider.invalid/v1/messages",
            {},
            {"Authorization": "Bearer must-not-cross-origin"},
        )

    redirect_handler = next(
        handler
        for handler in installed_handlers
        if isinstance(handler, llm._RejectRedirects)
    )
    initial = llm.urllib.request.Request(
        "https://api.provider.invalid/v1/messages",
        headers={"Authorization": "Bearer must-not-cross-origin"},
    )
    assert (
        redirect_handler.redirect_request(
            initial,
            None,
            302,
            "Found",
            {},
            "https://attacker.invalid/capture",
        )
        is None
    )


@pytest.mark.parametrize(
    ("provider", "payload", "message"),
    [
        ("openai", {"choices": []}, "no completion choice"),
        ("anthropic", {"content": []}, "no text content"),
    ],
)
def test_hosted_provider_rejects_malformed_success_payload(
    monkeypatch, provider, payload, message
):
    monkeypatch.setenv("ADDUCE_LLM_PROVIDER", provider)
    monkeypatch.setenv("ADDUCE_LLM_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_post_json", lambda *args, **kwargs: payload)

    with pytest.raises(llm.LLMUnavailable, match=message):
        llm.complete("summary")
