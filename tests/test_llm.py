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
