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

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from ..engine import CheckResult
from ..ledger import (
    EVIDENCE_ONLY_TEXT_POLICY,
    PROVIDER_UNVERIFIED_TEXT_POLICY,
    AnswerLevel,
    Ledger,
    LedgerEntry,
    build_entry,
    build_provenance,
    provider_text_provenance,
    sha256_text,
)
from ..markdown_safety import (
    markdown_code_span,
    markdown_indented_lines,
    markdown_inline,
)
from ..rules.base import Finding, Status

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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
    if not isinstance(data, dict):
        raise ValueError("Checklist must be a YAML mapping.")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Checklist must define at least one item.")
    items: list[ChecklistItem] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Checklist item {index} must be a mapping.")
        item_id = item.get("id")
        question = item.get("question")
        rules = item.get("rules", [])
        manual = item.get("manual", False)
        guidance = item.get("guidance", "")
        if (
            not isinstance(item_id, str)
            or not _IDENTIFIER_RE.fullmatch(item_id)
            or item_id in seen_ids
        ):
            raise ValueError(
                f"Checklist item {index} has an invalid or duplicate id."
            )
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Checklist item {index} must have a question.")
        if (
            not isinstance(rules, list)
            or any(not isinstance(rule_id, str) or not rule_id for rule_id in rules)
            or len(rules) != len(set(rules))
        ):
            raise ValueError(
                f"Checklist item {index} rules must be unique non-empty strings."
            )
        if not isinstance(manual, bool) or not isinstance(guidance, str):
            raise ValueError(
                f"Checklist item {index} has invalid manual or guidance metadata."
            )
        seen_ids.add(item_id)
        items.append(
            ChecklistItem(
                id=item_id,
                question=question,
                rules=list(rules),
                manual=manual,
                guidance=guidance.strip(),
            )
        )
    name = data.get("name", name_or_path)
    key = data.get("key", name_or_path)
    preamble = data.get("preamble", "")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(key, str)
        or not _IDENTIFIER_RE.fullmatch(key)
        or not isinstance(preamble, str)
    ):
        raise ValueError("Checklist name, key, and preamble metadata are invalid.")
    return Checklist(
        name=name,
        key=key,
        preamble=preamble.strip(),
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
        lines.append(
            f"[{marker}] {finding.rule_id}: {markdown_inline(finding.message)}"
        )
    return lines


def _anchors(findings: list[Finding], cap: int = 4) -> list[str]:
    """path:line anchors so a reviewer can jump from an answer to the source."""
    return [str(loc) for finding in findings for loc in finding.locations][:cap]


def _manifest_supports(findings: list[Finding], result: CheckResult) -> bool:
    """Whether confirmed manifest fields directly support every finding.

    A manifest created by ``adduce manifest`` contains draft claims. Those
    drafts must not be promoted to author-confirmed evidence merely because
    the file now exists. Support is deliberately narrow: only fields the
    scaffolder cannot safely invent, plus non-draft claim links, qualify.
    """
    if not findings or not result.evidence.manifest.exists:
        return False
    manifest = result.evidence.manifest
    confirmed_claims = [
        claim
        for claim in manifest.claims
        if (claim.status or "").strip().lower() == "confirmed"
    ]
    supported: set[str] = set()
    if manifest.smoke.command:
        supported.add("R-EXEC-002")
    if any(claim.produced_by.command for claim in confirmed_claims):
        supported.update({"R-EXEC-002", "R-EXEC-003", "R-RUN-001"})
    if any(claim.value is not None for claim in confirmed_claims):
        supported.add("R-DOC-003")
    if manifest.environment.hardware:
        supported.update({"R-PREC-005", "R-DRIFT-005"})
    return all(finding.rule_id in supported for finding in findings)


def render_markdown(
    checklist: Checklist,
    result: CheckResult,
    llm_drafts: dict[str, str] | None = None,
    llm_provider: tuple[str, str] | None = None,
    strict: bool = False,
) -> tuple[str, Ledger]:
    """Render the checklist draft and the evidence ledger behind it.

    ``llm_drafts`` optionally carries unverified provider prose keyed by item
    id. A provider/model identity is mandatory whenever such prose is present,
    and the evidence answers stay deterministic regardless. ``strict`` raises
    the evidence bar (see :func:`adduce.ledger.derive_answer`).
    """
    llm_drafts = llm_drafts or {}
    if llm_drafts and llm_provider is None:
        raise ValueError("provider-generated prose requires provider/model provenance")
    known_item_ids = {item.id for item in checklist.items}
    unknown_drafts = sorted(set(llm_drafts) - known_item_ids)
    if unknown_drafts:
        raise ValueError(
            "provider-generated prose targets unknown checklist item(s): "
            + ", ".join(unknown_drafts)
        )
    if llm_provider is not None and (
        not llm_provider[0].strip() or not llm_provider[1].strip()
    ):
        raise ValueError("provider-generated prose requires non-empty provider/model provenance")
    generated_text_provenance = (
        [
            provider_text_provenance(
                item_id,
                llm_provider[0],
                llm_provider[1],
                text,
            )
            for item_id, text in sorted(llm_drafts.items())
        ]
        if llm_provider is not None
        else []
    )
    provenance_by_item = {
        item.item_id: item for item in generated_text_provenance
    }
    findings_by_rule = {f.rule_id: f for f in result.card.findings}
    entries: list[LedgerEntry] = []
    lines: list[str] = []
    lines.append(f"# {markdown_inline(checklist.name)}")
    lines.append("")
    lines.append(
        f"Repository: {markdown_code_span(result.repo.root.name)}"
        + (
            " at commit "
            + markdown_code_span((result.repo.git.head_commit or "")[:7])
            if result.repo.git.head_commit
            else ""
        )
    )
    lines.append("")
    if checklist.preamble:
        lines.append(f"> {markdown_inline(checklist.preamble)}")
        lines.append("")

    for index, item in enumerate(checklist.items, start=1):
        lines.append(f"## {index}. {markdown_inline(item.question)}")
        lines.append("")
        item_findings = [findings_by_rule[r] for r in item.rules if r in findings_by_rule]
        entry = build_entry(
            item_id=item.id,
            question=item.question,
            findings=item_findings,
            rule_ids=item.rules,
            manifest_backed=_manifest_supports(item_findings, result),
            strict=strict,
            manual=item.manual,
        )
        entries.append(entry)
        lines.append(f"**Answer:** {_ANSWER_TEXT[entry.answer]}")
        anchors = _anchors(item_findings) if not item.manual else []
        if anchors:
            lines.append(
                "[EVIDENCE: "
                + ", ".join(markdown_code_span(anchor) for anchor in anchors)
                + "]"
            )
        if entry.conflicts:
            lines.append("[AUTHOR REVIEW REQUIRED] — conflicting evidence must be resolved")
        lines.append("")
        if item.id in llm_drafts:
            fragment = provenance_by_item[item.id]
            item_sha256 = sha256_text(item.id)
            lines.append(
                "<!-- adduce-provider-fragment "
                f'id-sha256="{item_sha256}" '
                f'text-sha256="{fragment.text_sha256}" -->'
            )
            lines.append(
                "**Unverified provider draft** "
                "([AUTHOR REVIEW REQUIRED] — this prose is not evidence):"
            )
            lines.append("")
            lines.extend(markdown_indented_lines(llm_drafts[item.id]))
            lines.append("")
        if item_findings:
            lines.append("**Repository evidence:**")
            lines.append("")
            for line in _evidence_lines(item_findings):
                lines.append(f"- {line}")
            lines.append("")
        if item.guidance:
            lines.append(f"_{markdown_inline(item.guidance)}_")
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
        generated_text_policy=(
            PROVIDER_UNVERIFIED_TEXT_POLICY
            if llm_drafts
            else EVIDENCE_ONLY_TEXT_POLICY
        ),
        generated_text_provenance=generated_text_provenance,
    )
    return markdown, ledger
