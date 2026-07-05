"""Portability evidence: things that only work on the author's machine.

Local absolute paths, hardcoded localhost endpoints, and committed secrets.
Secret detection records the location and kind only — the matched value is
never stored or echoed anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Repo

_SCANNABLE_SUFFIXES = frozenset({".py", ".sh", ".bash", ".yaml", ".yml", ".json", ".cfg", ".ini", ".env", ".toml"})
_MAX_SCAN_BYTES = 2_000_000

_ABS_PATH_RE = re.compile(
    r"(/Users/\w[\w.-]*|/home/(?!runner\b|user\b)\w[\w.-]*|[A-Z]:\\Users\\\w+|~/(?:Desktop|Documents|Downloads)\b|/Volumes/\w+)"
)
_LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d{2,5}\b")

#: (kind, pattern). Patterns match well-known token shapes, not generic entropy.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("W&B API key", re.compile(r"\bWANDB_API_KEY\s*[=:]\s*['\"]?[a-f0-9]{40}\b")),
)


@dataclass(frozen=True)
class PortabilityHit:
    kind: str    # abs_path | localhost | secret
    detail: str  # for secrets: the kind label only, never the value
    file: str
    line: int


@dataclass
class PortabilityEvidence:
    hits: list[PortabilityHit] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[PortabilityHit]:
        return [h for h in self.hits if h.kind == kind]


def collect_portability(repo: Repo) -> PortabilityEvidence:
    evidence = PortabilityEvidence()
    for entry in repo.files:
        if entry.suffix not in _SCANNABLE_SUFFIXES or entry.size > _MAX_SCAN_BYTES:
            continue
        rel = str(entry.path)
        if rel.startswith((".adduce/", "docs/")):
            continue
        text = repo.read_text(rel)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _ABS_PATH_RE.finditer(line):
                evidence.hits.append(
                    PortabilityHit("abs_path", match.group(0), rel, lineno)
                )
            if localhost := _LOCALHOST_RE.search(line):
                evidence.hits.append(PortabilityHit("localhost", localhost.group(0), rel, lineno))
            for kind, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    evidence.hits.append(PortabilityHit("secret", kind, rel, lineno))
    return evidence
