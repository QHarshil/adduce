"""The evidence ledger: what every generated answer rests on.

Generated artifacts (checklists, appendices, submission packages) are drafts
assembled from detected evidence, and a draft is only trustworthy when its
claims can be traced back to that evidence. The ledger records, per answered
item, which surfaces were consulted, which evidence supports the answer and
how strong it is, what is missing, and what conflicts — so a drafted "yes"
can never silently outrun its evidence, and ``adduce audit-generated`` can
re-check an artifact long after it was produced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from . import __version__
from .rules.base import Finding, Status

LEDGER_DIR = ".adduce"
LEDGER_NAME = "evidence-ledger.json"

# Confidence policy: a drafted "yes" needs strong evidence; anything weaker is
# explicitly partial or unknown so the author knows what still needs them.
_YES_MIN_CONFIDENCE = 0.85
_STRICT_YES_MIN_CONFIDENCE = 0.90
_PARTIAL_MIN_CONFIDENCE = 0.50
_DIRECT_MIN_CONFIDENCE = 0.80


class AnswerLevel(str, Enum):
    """How far the detected evidence supports an item — never a certification."""

    YES = "yes"
    PARTIAL = "partial"
    NOT_DETECTED = "not_detected"
    AUTHOR_INPUT_REQUIRED = "author_input_required"
    UNKNOWN = "unknown"


class EvidenceStrength(str, Enum):
    """Provenance class of one piece of evidence, ordered by trust."""

    DIRECT = "direct"
    INFERRED = "inferred"
    MANIFEST_AUTHOR_CONFIRMED = "manifest_author_confirmed"
    ONLINE_RESOLVED = "online_resolved"
    DYNAMIC_VERIFIED = "dynamic_verified"


@dataclass
class EvidenceItem:
    """One piece of evidence behind an answer: a rule hit or a manifest claim."""

    kind: str  # rule id, or "manifest"
    path: str
    line: int | None
    confidence: float
    strength: EvidenceStrength

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "confidence": self.confidence,
            "strength": self.strength.value,
        }


@dataclass
class LedgerEntry:
    """The full evidence account for one answered item."""

    item_id: str
    question: str
    answer: AnswerLevel
    evidence: list[EvidenceItem] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)  # evidence surfaces consulted
    missing: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "question": self.question,
            "answer": self.answer.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "searched": self.searched,
            "missing": self.missing,
            "conflicts": self.conflicts,
        }


@dataclass
class Ledger:
    """Everything needed to re-audit one generated artifact."""

    artifact_path: str
    artifact_sha256: str
    provenance: dict[str, Any]
    entries: list[LedgerEntry] = field(default_factory=list)
    generated_text_policy: str = "evidence_only"

    def counts(self) -> dict[str, int]:
        """Tally of answers by level plus the number of conflicted entries."""
        tally = {
            "evidence_backed": 0,
            "partial": 0,
            "author_input_required": 0,
            "not_detected": 0,
            "unknown": 0,
            "conflicts": 0,
        }
        keys = {
            AnswerLevel.YES: "evidence_backed",
            AnswerLevel.PARTIAL: "partial",
            AnswerLevel.AUTHOR_INPUT_REQUIRED: "author_input_required",
            AnswerLevel.NOT_DETECTED: "not_detected",
            AnswerLevel.UNKNOWN: "unknown",
        }
        for entry in self.entries:
            tally[keys[entry.answer]] += 1
            if entry.conflicts:
                tally["conflicts"] += 1
        return tally

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "provenance": self.provenance,
            "generated_text_policy": self.generated_text_policy,
            "counts": self.counts(),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def sha256_text(text: str) -> str:
    """SHA-256 of an artifact exactly as it is written to disk.

    Renderers and the CLI writer normalise trailing newlines identically, so
    the recorded hash matches the emitted file byte for byte.
    """
    return hashlib.sha256((text.rstrip("\n") + "\n").encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_provenance(
    command: str,
    profile: str | None,
    mode: str,
    repo_commit: str | None,
) -> dict[str, Any]:
    """Record how a draft was produced, so a stale one is distinguishable."""
    return {
        "adduce_version": __version__,
        "command": command,
        "profile": profile,
        "mode": mode,
        "repo_commit": repo_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def evidence_from_findings(findings: Sequence[Finding], manifest_backed: bool) -> list[EvidenceItem]:
    """Evidence items for the findings that support an answer.

    Strength policy: manifest claims are author-confirmed; a finding is direct
    only when it is both confident and pinned to source locations — everything
    else is an inference and must be labelled as one.
    """
    items: list[EvidenceItem] = []
    for finding in findings:
        if finding.status not in (Status.PASS, Status.PARTIAL):
            continue
        strength = (
            EvidenceStrength.DIRECT
            if finding.confidence >= _DIRECT_MIN_CONFIDENCE and finding.locations
            else EvidenceStrength.INFERRED
        )
        if finding.locations:
            items.extend(
                EvidenceItem(
                    kind=finding.rule_id,
                    path=location.path,
                    line=location.line,
                    confidence=finding.confidence,
                    strength=strength,
                )
                for location in finding.locations[:4]
            )
        else:
            items.append(
                EvidenceItem(
                    kind=finding.rule_id,
                    path="",
                    line=None,
                    confidence=finding.confidence,
                    strength=strength,
                )
            )
    if manifest_backed:
        items.append(
            EvidenceItem(
                kind="manifest",
                path=f"{LEDGER_DIR}/manifest.yaml",
                line=None,
                confidence=1.0,
                strength=EvidenceStrength.MANIFEST_AUTHOR_CONFIRMED,
            )
        )
    return items


def derive_answer(
    findings: list[Finding],
    manifest_backed: bool,
    strict: bool = False,
) -> tuple[AnswerLevel, list[EvidenceItem]]:
    """Derive the strongest answer the evidence can honestly carry.

    Default policy: yes only when every scored finding passes and either the
    weakest confidence is >= 0.85 or the author-confirmed manifest backs the
    item; mixed results or confidence in [0.5, 0.85) draft as partial; all
    failures draft as not detected; findings that carry no score leave the
    item unknown. Strict mode raises the yes bar to 0.90 and hands anything
    resting purely on inference back to the author.
    """
    evidence = evidence_from_findings(findings, manifest_backed)
    scored = [f for f in findings if f.status.score_value is not None]
    if not scored:
        return AnswerLevel.UNKNOWN, evidence

    values = [f.status.score_value for f in scored]
    min_confidence = min(f.confidence for f in scored)
    yes_threshold = _STRICT_YES_MIN_CONFIDENCE if strict else _YES_MIN_CONFIDENCE

    if all(v == 0.0 for v in values):
        answer = AnswerLevel.NOT_DETECTED
    elif all(v == 1.0 for v in values):
        if manifest_backed or min_confidence >= yes_threshold:
            answer = AnswerLevel.YES
        elif min_confidence >= _PARTIAL_MIN_CONFIDENCE:
            answer = AnswerLevel.PARTIAL
        else:
            answer = AnswerLevel.UNKNOWN
    else:
        answer = AnswerLevel.PARTIAL

    if (
        strict
        and answer in (AnswerLevel.YES, AnswerLevel.PARTIAL)
        and all(item.strength is EvidenceStrength.INFERRED for item in evidence)
    ):
        return AnswerLevel.AUTHOR_INPUT_REQUIRED, evidence
    return answer, evidence


def build_entry(
    item_id: str,
    question: str,
    findings: list[Finding],
    rule_ids: Sequence[str],
    manifest_backed: bool,
    strict: bool = False,
    manual: bool = False,
) -> LedgerEntry:
    """Assemble the ledger entry for one item.

    Manual items are decided here rather than in :func:`derive_answer` because
    "the repository cannot know this" is a property of the question, not of
    the evidence found for it.
    """
    searched = list(rule_ids)
    if manifest_backed:
        searched.append("manifest")
    if manual:
        answer: AnswerLevel = AnswerLevel.AUTHOR_INPUT_REQUIRED
        evidence: list[EvidenceItem] = []
    else:
        answer, evidence = derive_answer(findings, manifest_backed, strict)
    missing = [f.message for f in findings if f.status is Status.FAIL]
    conflicts = [
        f.message
        for f in findings
        if (f.rule_id.startswith("R-DRIFT-") or f.rule_id == "R-RES-002")
        and f.status in (Status.PARTIAL, Status.FAIL)
    ]
    return LedgerEntry(
        item_id=item_id,
        question=question,
        answer=answer,
        evidence=evidence,
        searched=searched,
        missing=missing,
        conflicts=conflicts,
    )


def write_ledger(root: Path, ledger: Ledger) -> Path:
    """Write (or update) ``.adduce/evidence-ledger.json``.

    The file is keyed by artifact path so the checklist and appendix ledgers
    for one repository coexist instead of overwriting each other.
    """
    directory = root / LEDGER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / LEDGER_NAME
    records = load_ledger(root)
    records[ledger.artifact_path] = ledger.to_dict()
    target.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return target


def load_ledger(root: Path) -> dict[str, Any]:
    """Load the ledger file as a raw dict keyed by artifact path; {} if absent."""
    target = root / LEDGER_DIR / LEDGER_NAME
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}
