"""Optional LLM layer: prose only, never checks or scores.

Strictly separated from the deterministic core. With no provider configured,
every command works identically — the LLM only (a) drafts the free-text
justification fields in checklists and appendices and (b) summarises a
repository's posture for PR comments. Bring-your-own-key: adduce ships no
key and never calls a paid API on your behalf.

Configuration (environment):
    ADDUCE_LLM_PROVIDER   openai | anthropic | ollama
    ADDUCE_LLM_MODEL      provider model name (defaults per provider)
    OPENAI_API_KEY / ANTHROPIC_API_KEY   for the hosted providers
    ADDUCE_OLLAMA_URL     defaults to http://localhost:11434
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_TIMEOUT_SECONDS = 60

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "ollama": "llama3.1",
}


class LLMUnavailable(RuntimeError):
    """No provider configured, or the provider call failed."""


def provider_configured() -> str | None:
    provider = os.environ.get("ADDUCE_LLM_PROVIDER", "").lower() or None
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if provider == "ollama":
        return "ollama"
    return None


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(str(exc)) from exc


def complete(prompt: str, max_tokens: int = 500) -> str:
    """One prompt, one completion, provider-agnostic. Raises LLMUnavailable."""
    provider = provider_configured()
    if provider is None:
        raise LLMUnavailable(
            "No LLM provider configured. Set ADDUCE_LLM_PROVIDER (openai|anthropic|ollama) "
            "and the matching API key; everything works without one."
        )
    model = os.environ.get("ADDUCE_LLM_MODEL") or _DEFAULT_MODELS[provider]

    if provider == "openai":
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        )
        return data["choices"][0]["message"]["content"].strip()
    if provider == "anthropic":
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        )
        return "".join(block.get("text", "") for block in data.get("content", [])).strip()
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
        "Repository evidence (produced by static analysis; treat as ground truth, add nothing):\n"
        + "\n".join(f"- {line}" for line in evidence_lines)
        + "\n\nWrite 2-3 sentences of justification strictly from this evidence, in the first "
        "person plural ('we provide...'). If evidence is missing, say what is missing plainly."
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
