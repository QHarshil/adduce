"""Conference checklist drafting from repository evidence.

Each bundled checklist maps its items to rule IDs. Items are answered from
finding statuses through the evidence-ledger policy (all pass with strong
evidence → yes, mixed or weakly supported → partial, all fail → not
detected); items the repository cannot answer are handed to the authors
explicitly. Rendering returns the ledger alongside the markdown so every
drafted answer stays traceable to the evidence it rests on. The output is a
draft: honest wording about that is part of the design, not a disclaimer
bolted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from ..engine import CheckResult
from ..ledger import (
    AnswerLevel,
    Ledger,
    LedgerEntry,
    build_entry,
    build_provenance,
    sha256_text,
)
from ..rules.base import Finding, Status


@dataclass
class ChecklistItem:
    id: str
    question: str
    rules: list[str] = field(default_factory=list)
    manual: bool = False
    guidance: str = ""


@dataclass
class Checklist:
    name: str
    key: str
    preamble: str
    items: list[ChecklistItem]


def available_checklists() -> list[str]:
    return sorted(
        entry.name[: -len(".yaml")]
        for entry in resources.files(__package__).iterdir()
        if entry.name.endswith(".yaml")
    )


def load_checklist(name_or_path: str) -> Checklist:
    path = Path(name_or_path)
    if path.suffix in {".yaml", ".yml"} and path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        resource = resources.files(__package__).joinpath(f"{name_or_path}.yaml")
        if not resource.is_file():
            raise ValueError(
                f"Unknown checklist '{name_or_path}'. Bundled: {', '.join(available_checklists())}."
            )
        text = resource.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    items = [
        ChecklistItem(
            id=item["id"],
            question=item["question"],
            rules=list(item.get("rules", [])),
            manual=bool(item.get("manual", False)),
            guidance=str(item.get("guidance", "")).strip(),
        )
        for item in data.get("items", [])
    ]
    return Checklist(
        name=data.get("name", name_or_path),
        key=data.get("key", name_or_path),
        preamble=str(data.get("preamble", "")).strip(),
        items=items,
    )


_ANSWER_TEXT = {
    AnswerLevel.YES: "Yes (draft)",
    AnswerLevel.PARTIAL: "Partial (draft)",
    AnswerLevel.NOT_DETECTED: "Not detected (draft)",
    AnswerLevel.UNKNOWN: "Unknown (draft)",
    AnswerLevel.AUTHOR_INPUT_REQUIRED: (
        "[AUTHOR REVIEW REQUIRED] — depends on information outside the repository"
    ),
}


def _evidence_lines(findings: list[Finding]) -> list[str]:
    """Human-readable evidence bullets, one per consulted finding."""
    lines = []
    for finding in findings:
        marker = {Status.PASS: "found", Status.PARTIAL: "partial", Status.FAIL: "missing"}.get(
            finding.status, "n/a"
        )
        lines.append(f"[{marker}] {finding.rule_id}: {finding.message}")
    return lines


def _anchors(findings: list[Finding], cap: int = 4) -> list[str]:
    """path:line anchors so a reviewer can jump from an answer to the source."""
    return [str(loc) for finding in findings for loc in finding.locations][:cap]


def render_markdown(
    checklist: Checklist,
    result: CheckResult,
    llm_drafts: dict[str, str] | None = None,
    strict: bool = False,
) -> tuple[str, Ledger]:
    """Render the filled checklist and the evidence ledger behind it.

    ``llm_drafts`` optionally carries LLM-phrased justification prose keyed by
    item id; the evidence answers stay deterministic regardless. ``strict``
    raises the evidence bar (see :func:`adduce.ledger.derive_answer`).
    """
    llm_drafts = llm_drafts or {}
    findings_by_rule = {f.rule_id: f for f in result.card.findings}
    manifest_backed = bool(result.evidence.manifest.claims)
    entries: list[LedgerEntry] = []
    lines: list[str] = []
    lines.append(f"# {checklist.name}")
    lines.append("")
    lines.append(f"Repository: `{result.repo.root.name}`"
                 + (f" at commit `{(result.repo.git.head_commit or '')[:7]}`" if result.repo.git.head_commit else ""))
    lines.append("")
    if checklist.preamble:
        lines.append(f"> {checklist.preamble}")
        lines.append("")

    for index, item in enumerate(checklist.items, start=1):
        lines.append(f"## {index}. {item.question}")
        lines.append("")
        item_findings = [findings_by_rule[r] for r in item.rules if r in findings_by_rule]
        entry = build_entry(
            item_id=item.id,
            question=item.question,
            findings=item_findings,
            rule_ids=item.rules,
            manifest_backed=manifest_backed,
            strict=strict,
            manual=item.manual,
        )
        entries.append(entry)
        lines.append(f"**Answer:** {_ANSWER_TEXT[entry.answer]}")
        anchors = _anchors(item_findings) if not item.manual else []
        if anchors:
            lines.append(f"[EVIDENCE: {', '.join(anchors)}]")
        lines.append("")
        if item.id in llm_drafts:
            lines.append("**Draft justification** (LLM-phrased from the evidence below — check it):")
            lines.append("")
            lines.append(llm_drafts[item.id])
            lines.append("")
        if item_findings:
            lines.append("**Repository evidence:**")
            lines.append("")
            for line in _evidence_lines(item_findings):
                lines.append(f"- {line}")
            lines.append("")
        if item.guidance:
            lines.append(f"_{item.guidance}_")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Drafted from static repository evidence. Verify each answer against the "
        "paper before submission; answers about the paper text cannot be derived here."
    )
    lines.append("")
    markdown = "\n".join(lines)
    ledger = Ledger(
        artifact_path=f"checklist-{checklist.key}.md",
        artifact_sha256=sha256_text(markdown),
        provenance=build_provenance(
            command="checklist",
            profile=checklist.key,
            mode="strict" if strict else "default",
            repo_commit=result.repo.git.head_commit,
        ),
        entries=entries,
    )
    return markdown, ledger
