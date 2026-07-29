"""Optional LLM layer: prose only, never checks or scores.

Strictly separated from the deterministic core. With no provider configured,
every command works identically—the provider integration only drafts optional
free-text checklist justifications. Bring-your-own-key: Adduce ships no key and
makes no provider request unless the user selects ``--llm`` and configures one.

Configuration (environment):
    ADDUCE_LLM_PROVIDER   openai | anthropic | ollama
    ADDUCE_LLM_MODEL      provider model name (required for hosted providers)
    OPENAI_API_KEY / ANTHROPIC_API_KEY   for the hosted providers
    ADDUCE_OLLAMA_URL     defaults to http://localhost:11434
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

_TIMEOUT_SECONDS = 60
_MAX_RESPONSE_BYTES = 1 << 20

_DEFAULT_LOCAL_MODEL = "llama3.1"


class LLMUnavailable(RuntimeError):
    """No provider configured, or the provider call failed."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so provider credentials never cross an origin boundary."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class ProviderIdentity:
    """Non-secret provider metadata recorded beside unverified model prose."""

    provider: str
    model: str


def provider_configured() -> str | None:
    provider = os.environ.get("ADDUCE_LLM_PROVIDER", "").lower() or None
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if provider == "ollama":
        return "ollama"
    return None


def provider_identity() -> ProviderIdentity:
    """Return the configured provider and model without exposing credentials."""
    provider = provider_configured()
    if provider is None:
        raise LLMUnavailable(
            "No LLM provider configured. Set ADDUCE_LLM_PROVIDER (openai|anthropic|ollama) "
            "and the matching API key; everything works without one."
        )
    model = os.environ.get("ADDUCE_LLM_MODEL")
    if not model:
        if provider == "ollama":
            model = _DEFAULT_LOCAL_MODEL
        else:
            raise LLMUnavailable(
                "Set ADDUCE_LLM_MODEL to an API model identifier supported by the configured provider."
            )
    return ProviderIdentity(provider=provider, model=model)


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            response_payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(response_payload) > _MAX_RESPONSE_BYTES:
                raise LLMUnavailable("provider response exceeds the 1 MiB limit")
            parsed = json.loads(response_payload.decode("utf-8"))
    except (
        urllib.error.URLError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise LLMUnavailable(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise LLMUnavailable("provider response is not a JSON object")
    return parsed


def _openai_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMUnavailable("OpenAI response has no completion choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMUnavailable("OpenAI response has no text content")
    return content.strip()


def _anthropic_text(data: dict[str, Any]) -> str:
    content = data.get("content")
    if not isinstance(content, list):
        raise LLMUnavailable("Anthropic response has no content blocks")
    text_parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    if not text_parts:
        raise LLMUnavailable("Anthropic response has no text content")
    return "".join(text_parts).strip()


def complete(prompt: str, max_tokens: int = 500) -> str:
    """One prompt, one completion, provider-agnostic. Raises LLMUnavailable."""
    identity = provider_identity()
    provider = identity.provider
    model = identity.model

    if provider == "openai":
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        )
        return _openai_text(data)
    if provider == "anthropic":
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        )
        return _anthropic_text(data)
    # ollama
    base = os.environ.get("ADDUCE_OLLAMA_URL", "http://localhost:11434")
    data = _post_json(
        f"{base}/api/generate",
        {"model": model, "prompt": prompt, "stream": False},
        {},
    )
    return str(data.get("response", "")).strip()


def draft_justification(question: str, evidence_lines: list[str]) -> str:
    """A checklist justification drafted from the deterministic evidence.

    The evidence lines are adduce's own findings; the model only phrases
    them — it is instructed not to add claims of its own.
    """
    prompt = (
        "You are drafting the justification field of a conference reproducibility checklist.\n"
        f"Checklist question: {question}\n"
        "Static-analysis observations (these may be incomplete or incorrect):\n"
        + "\n".join(f"- {line}" for line in evidence_lines)
        + "\n\nWrite 2-3 cautious sentences strictly from these observations. Do not convert "
        "a detection into a claim that execution occurred or that the artifact is reproducible. "
        "Preserve every stated limitation, conflict, and missing item. If support is incomplete, "
        "say so plainly. This text will be labelled as unverified and must be reviewed by an author."
    )
    return complete(prompt, max_tokens=220)


def summarize_posture(score_line: str, top_findings: list[str]) -> str:
    """A short PR-comment style summary of the repository's posture."""
    prompt = (
        "Summarise this repository's reproducibility posture in 3 sentences for a pull-request "
        "comment. Be concrete and neutral; do not invent details.\n"
        f"Score: {score_line}\nTop findings:\n" + "\n".join(f"- {line}" for line in top_findings)
    )
    return complete(prompt, max_tokens=200)
