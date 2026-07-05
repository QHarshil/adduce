"""Conference checklist drafting from repository evidence.

Each bundled checklist maps its items to rule IDs. Items are answered from
finding statuses (all pass → Yes, mixed → Partial, all fail → No); items the
repository cannot answer are marked for the authors explicitly. The output
is a draft: honest wording about that is part of the design, not a
disclaimer bolted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from ..engine import CheckResult
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


def _draft_answer(findings: list[Finding]) -> tuple[str, list[str]]:
    """Derive a draft answer and its supporting evidence lines."""
    scored = [f for f in findings if f.status.score_value is not None]
    evidence = []
    for finding in findings:
        marker = {Status.PASS: "found", Status.PARTIAL: "partial", Status.FAIL: "missing"}.get(
            finding.status, "n/a"
        )
        evidence.append(f"[{marker}] {finding.rule_id}: {finding.message}")
    if not scored:
        return "No repository evidence", evidence
    values = [f.status.score_value for f in scored]
    if all(v == 1.0 for v in values):
        return "Yes (draft)", evidence
    if all(v == 0.0 for v in values):
        return "No (draft)", evidence
    return "Partial (draft)", evidence


def render_markdown(
    checklist: Checklist,
    result: CheckResult,
    llm_drafts: dict[str, str] | None = None,
) -> str:
    """Render the filled checklist. ``llm_drafts`` optionally carries
    LLM-phrased justification prose keyed by item id; the yes/no evidence
    answers stay deterministic regardless."""
    llm_drafts = llm_drafts or {}
    findings_by_rule = {f.rule_id: f for f in result.card.findings}
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
        if item.manual:
            lines.append("**Answer:** _requires author input — not derivable from the repository._")
        else:
            answer, _ = _draft_answer(item_findings)
            lines.append(f"**Answer:** {answer}")
        lines.append("")
        if item.id in llm_drafts:
            lines.append("**Draft justification** (LLM-phrased from the evidence below — verify):")
            lines.append("")
            lines.append(llm_drafts[item.id])
            lines.append("")
        if item_findings:
            lines.append("**Repository evidence:**")
            lines.append("")
            _, evidence = _draft_answer(item_findings)
            for line in evidence:
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
    return "\n".join(lines)
