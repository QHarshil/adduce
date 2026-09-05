#!/usr/bin/env python3
"""Refuse reviewer-facing material that carries coordinator-only information.

A blinded reviewer who has seen the answer key is no longer blind and the review
that reviewer returns is void, so this check runs in CI over the tracked
reviewer documents and over any generated packet.  Detection is role-based: an
explicit table binds each reviewer-facing path, and each packet path, to a role,
and every rule declares the roles it applies to.  Filenames are not the control;
the role table, the packet manifest's declared roles and the path allowlist are.

Fenced code blocks are scanned exactly like the surrounding prose.  Every rule
here targets material that unblinds a reviewer however it is presented, and a
pasted coordinator transcript is most likely to arrive inside a fence, so a
fence exemption would open the widest hole in the check rather than remove a
false positive.  This module is standard-library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

if __package__:
    from .reviewer_packet import (
        CHECKLIST_NAME,
        CLAIMS_NAME,
        GUIDE_NAME,
        MANIFEST_NAME,
        PACKET_ROLES,
        REVIEW_SCHEMA_NAME,
        ROLE_BY_PACKET_PATH,
        SCAFFOLD_NAME,
        VERIFY_WRAPPER_NAME,
        WORKSPACE_SCHEMA_NAME,
    )
    from .run_contract import RunContractError, load_json_object_bytes
else:
    from reviewer_packet import (
        CHECKLIST_NAME,
        CLAIMS_NAME,
        GUIDE_NAME,
        MANIFEST_NAME,
        PACKET_ROLES,
        REVIEW_SCHEMA_NAME,
        ROLE_BY_PACKET_PATH,
        SCAFFOLD_NAME,
        VERIFY_WRAPPER_NAME,
        WORKSPACE_SCHEMA_NAME,
    )
    from run_contract import RunContractError, load_json_object_bytes

FINDING_GUIDE_NAME = "FINDING_REVIEW_GUIDE.md"
EFFECTIVENESS_METRICS_NAME = "EFFECTIVENESS_METRICS.md"

# Scan roles.  Every role a packet also names is the packet builder's own, so a
# rename there cannot silently unscope a rule here.
GUIDE_ROLE = ROLE_BY_PACKET_PATH[GUIDE_NAME]
CHECKLIST_ROLE = ROLE_BY_PACKET_PATH[CHECKLIST_NAME]
CLAIM_RECORD_ROLE = ROLE_BY_PACKET_PATH[CLAIMS_NAME]
FINDING_GUIDE_ROLE = "finding_review_guide"
PACKET_DATA_ROLE = "packet_data"
UNEXPECTED_ROLE = "unexpected_packet_file"
COORDINATOR_ONLY_ROLE = "coordinator_only"

CLAIM_GATE_ROLES = frozenset({GUIDE_ROLE, CHECKLIST_ROLE})
REVIEWER_DOCUMENT_ROLES = frozenset({GUIDE_ROLE, CHECKLIST_ROLE, FINDING_GUIDE_ROLE})
# A packet holds exactly its allowlisted files, so anything else in one is
# scanned as prose handed to a reviewer: the strictest rule set applies.
PROSE_ROLES = REVIEWER_DOCUMENT_ROLES | {UNEXPECTED_ROLE}
EVERY_SCANNED_ROLE = PROSE_ROLES | {CLAIM_RECORD_ROLE, PACKET_DATA_ROLE}

# The manifest role vocabulary is the packet builder's, not a second copy.
MANIFEST_ROLE_ALLOWLIST = frozenset(role for _, role in PACKET_ROLES)

ROOT_REVIEWER_SOURCES: tuple[tuple[str, str], ...] = (
    (f"corpus/review/{GUIDE_NAME}", GUIDE_ROLE),
    (f"corpus/review/{CHECKLIST_NAME}", CHECKLIST_ROLE),
    (f"corpus/review/{FINDING_GUIDE_NAME}", FINDING_GUIDE_ROLE),
)
COORDINATOR_ONLY_SOURCES: tuple[tuple[str, str], ...] = (
    (f"corpus/review/{EFFECTIVENESS_METRICS_NAME}", COORDINATOR_ONLY_ROLE),
)
PACKET_SCAN_ROLES: tuple[tuple[str, str], ...] = (
    (GUIDE_NAME, GUIDE_ROLE),
    (CHECKLIST_NAME, CHECKLIST_ROLE),
    (FINDING_GUIDE_NAME, FINDING_GUIDE_ROLE),
    (CLAIMS_NAME, CLAIM_RECORD_ROLE),
    (SCAFFOLD_NAME, PACKET_DATA_ROLE),
    (REVIEW_SCHEMA_NAME, PACKET_DATA_ROLE),
    (WORKSPACE_SCHEMA_NAME, PACKET_DATA_ROLE),
    (VERIFY_WRAPPER_NAME, PACKET_DATA_ROLE),
    (MANIFEST_NAME, PACKET_DATA_ROLE),
)

COORDINATOR_DOCUMENT_RULE = "coordinator-document"
UNLISTED_MANIFEST_ROLE_RULE = "unlisted-manifest-role"
PACKET_RULES: tuple[tuple[str, str], ...] = (
    (
        COORDINATOR_DOCUMENT_RULE,
        "a packet carries a coordinator-only document, a merged review, "
        "an adjudication or a candidate report",
    ),
    (
        UNLISTED_MANIFEST_ROLE_RULE,
        "a packet manifest declares a role outside the packet role allowlist",
    ),
)
# Applied to every packet-relative path, file or directory.  Each coordinator-only
# source bans its own name, so adding one to COORDINATOR_ONLY_SOURCES is enough to
# keep it out of every packet.
COORDINATOR_DOCUMENT_MARKERS: tuple[tuple[str, str], ...] = (
    *(
        (Path(source).stem.lower(), "a coordinator-only document")
        for source, _ in COORDINATOR_ONLY_SOURCES
    ),
    ("pilot-claim-links", "withheld claim-link output over the record under review"),
    ("claim-evaluation", "withheld claim-link output over the record under review"),
    ("merged", "a merged review"),
    ("adjudicat", "an adjudication record"),
    ("answer-key", "an answer-key summary"),
    ("coordinator", "a coordinator-only document"),
    ("determinism", "a candidate report"),
    ("combined.csv", "candidate aggregate results"),
    ("run_meta.json", "candidate run metadata"),
)

_SNIPPET_LIMIT = 120
# Two lines either side of a count keeps a three-row distribution table together
# without reaching across a paragraph of prose.
_AGGREGATE_WINDOW = 3

_RESOLUTIONS = r"unresolved|not_applicable|not-applicable|resolved"
_RESOLUTION_WORD_RE = re.compile(rf"\b(?:{_RESOLUTIONS})\b")
_RESOLUTION_COUNT_RE = re.compile(
    rf"\b(?P<before>{_RESOLUTIONS})\b[\s`'\"*:=|/(\[\],x-]{{0,4}}\d+"
    rf"|\d+[\s`'\"*:=|/(\[\],x-]{{0,4}}\b(?P<after>{_RESOLUTIONS})\b"
)
_COUNTER_RE = re.compile(r"\bCounter\s*\(")
_ANSWER_MAP_RE = re.compile(r"(?<![0-9A-Za-z])[RUN](?:[ \t,|]+[RUN]){9}(?![0-9A-Za-z])")
_TRAIL_STATUS_RE = re.compile(r"trail[ _-]status(?:es)?", re.IGNORECASE)
_TRAIL_VALUE_RE = re.compile(r"\b(?:supported|unlinked|partial|unknown)\b", re.IGNORECASE)
_UNIVERSAL_RE = re.compile(r"\b(?:all|every|each|uniform|identical)\b", re.IGNORECASE)
_QUANTIFIED_CLAIMS_RE = re.compile(
    r"\b(?:all|every|each)\b(?:\s+\w+){0,3}\s+claims?\b", re.IGNORECASE
)
_WITHHELD_RE = re.compile(
    r"pilot-claim-links[A-Za-z0-9._-]*"
    r"|pilot-claim-review-[A-Za-z0-9._-]*(?:merged|reviewer-[a-z0-9]+)[A-Za-z0-9._-]*",
    re.IGNORECASE,
)
_COORDINATOR_COMMAND_RE = re.compile(
    r"claim_review\.py[ \t]+merge"
    r"|claim_ground_truth\.py[ \t]+evaluate"
    r"|--require-accepted\b"
    r"|--require-complete\b"
)
_CANDIDATE_RUN_RE = re.compile(
    r"pilot-[0-9][0-9A-Za-z.]*-(?:r[0-9]+|ops)-[a-z0-9]+|run_validation\.py"
)
_FACTS_APPENDIX_RE = re.compile(r"facts[ _-]appendix", re.IGNORECASE)
_ADJUDICATION_WORD_RE = re.compile(r"\badjudicat\w*", re.IGNORECASE)
_ADJUDICATION_INSTRUCTION_RE = re.compile(
    r"\bdisagreement\w*\b[^.]{0,80}?\b(?:resolv|settl|reconcil|adjudicat)\w*"
    r"|\b(?:resolv|settl|reconcil)\w*\b[^.]{0,80}?\bdisagreement"
    r"|\b(?:both|two)\s+reviewers?['’]?s?\s+"
    r"(?:decisions|labels|records|judgements|judgments|answers|values)\b",
    re.IGNORECASE,
)
# Coordinator notes, administrative records and other local working state live in
# hidden directories that are excluded from the repository, so a reviewer document
# citing one is pointing at material outside the study artifact. Match any hidden
# directory and allow the few that are tracked and public, rather than naming the
# local ones: the set of local directories varies per coordinator, and a rule that
# enumerated them would pass the ones it had not been told about.
_PUBLIC_DOT_DIRECTORIES = frozenset({".adduce", ".github"})
_PROJECT_PATH_RE = re.compile(r"(?<![\w.-])(\.[a-z][a-z0-9_-]*)/")
_HOME_PATH_RE = re.compile(
    r"(?<![\w.~-])/(?:Users|home)/[A-Za-z0-9._-]+|(?<!\w)[A-Za-z]:\\Users\\"
)


class ReviewMaterialsError(ValueError):
    """Reviewer-facing material cannot be checked at all."""


@dataclass(frozen=True)
class Finding:
    """One leak of coordinator-only material into reviewer-facing material."""

    path: str
    line: int
    rule: str
    matched: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {_snippet(self.matched)}"


Matcher = Callable[[Sequence[str], int, str], "str | None"]


@dataclass(frozen=True)
class Rule:
    """One detection, the reason it exists, and the roles it applies to."""

    identifier: str
    reason: str
    roles: frozenset[str]
    matcher: Matcher


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _SNIPPET_LIMIT:
        return collapsed
    return collapsed[:_SNIPPET_LIMIT] + "..."


def _pattern_matcher(pattern: re.Pattern[str]) -> Matcher:
    def matcher(lines: Sequence[str], index: int, role: str) -> str | None:
        found = pattern.search(lines[index])
        return found.group(0) if found is not None else None

    return matcher


def _paired_resolutions(line: str) -> dict[str, str]:
    """Map each resolution value on *line* that sits beside a count to its match."""
    paired: dict[str, str] = {}
    for found in _RESOLUTION_COUNT_RE.finditer(line):
        word = (found.group("before") or found.group("after")).lower().replace("-", "_")
        paired.setdefault(word, found.group(0))
    return paired


def _resolution_aggregate(lines: Sequence[str], index: int, role: str) -> str | None:
    line = lines[index]
    counter = _COUNTER_RE.search(line)
    resolution = _RESOLUTION_WORD_RE.search(line)
    if counter is not None and resolution is not None:
        return f"{counter.group(0)} {resolution.group(0)}"
    paired = _paired_resolutions(line)
    if not paired:
        return None
    if len(paired) > 1:
        return "; ".join(paired[word] for word in sorted(paired))
    word, matched = next(iter(paired.items()))
    for back in range(1, min(_AGGREGATE_WINDOW, index) + 1):
        earlier = _paired_resolutions(lines[index - back])
        other = sorted(set(earlier) - {word})
        if other:
            return f"{earlier[other[0]]}; {matched}"
    return None


def _uniform_trail_status(lines: Sequence[str], index: int, role: str) -> str | None:
    line = lines[index]
    universal = _UNIVERSAL_RE.search(line)
    trail = _TRAIL_STATUS_RE.search(line)
    if universal is not None and trail is not None:
        return f"{universal.group(0)} ... {trail.group(0)}"
    claims = _QUANTIFIED_CLAIMS_RE.search(line)
    value = _TRAIL_VALUE_RE.search(line)
    if claims is not None and value is not None:
        return f"{claims.group(0)} ... {value.group(0)}"
    return None


def _adjudication_instruction(lines: Sequence[str], index: int, role: str) -> str | None:
    line = lines[index]
    # The bare word is decisive only in prose.  A packet's review scaffold
    # carries an `adjudication` field and its schemas name the adjudication
    # record, so matching the word there would refuse every real packet.
    if role in CLAIM_GATE_ROLES or role == UNEXPECTED_ROLE:
        word = _ADJUDICATION_WORD_RE.search(line)
        if word is not None:
            return word.group(0)
    instruction = _ADJUDICATION_INSTRUCTION_RE.search(line)
    return instruction.group(0) if instruction is not None else None


def _coordinator_path(lines: Sequence[str], index: int, role: str) -> str | None:
    line = lines[index]
    for project in _PROJECT_PATH_RE.finditer(line):
        if project.group(1) not in _PUBLIC_DOT_DIRECTORIES:
            return project.group(0)
    # The frozen claim record quotes artifact identifiers taken from the
    # repositories under review, and an upstream absolute home path there is the
    # evidence the reviewer is judging rather than a coordinator location.
    if role == CLAIM_RECORD_ROLE:
        return None
    home = _HOME_PATH_RE.search(line)
    return home.group(0) if home is not None else None


RULES: tuple[Rule, ...] = (
    Rule(
        identifier="answer-map",
        reason="a compact expected-resolution map for a claim under review",
        roles=PROSE_ROLES,
        matcher=_pattern_matcher(_ANSWER_MAP_RE),
    ),
    Rule(
        identifier="resolution-aggregate",
        reason="an aggregate distribution over the expected resolutions",
        roles=PROSE_ROLES,
        matcher=_resolution_aggregate,
    ),
    Rule(
        identifier="uniform-trail-status",
        reason="a claim that every claim in the record shares one trail status",
        roles=PROSE_ROLES,
        matcher=_uniform_trail_status,
    ),
    Rule(
        identifier="withheld-report",
        reason="material withheld for the duration of the gate: claim-link output, "
        "a merged review, or a peer reviewer's file",
        roles=PROSE_ROLES,
        matcher=_pattern_matcher(_WITHHELD_RE),
    ),
    Rule(
        identifier="coordinator-command",
        reason="a coordinator step that joins or accepts reviewer answers",
        roles=PROSE_ROLES,
        matcher=_pattern_matcher(_COORDINATOR_COMMAND_RE),
    ),
    Rule(
        identifier="candidate-execution",
        reason="a candidate run label or a candidate execution command",
        roles=PROSE_ROLES,
        matcher=_pattern_matcher(_CANDIDATE_RUN_RE),
    ),
    Rule(
        identifier="facts-appendix",
        reason="a derived answer-shaped digest of the record under review",
        roles=EVERY_SCANNED_ROLE,
        matcher=_pattern_matcher(_FACTS_APPENDIX_RE),
    ),
    # Deliberately not applied to `finding_review_guide`.  The finding-review
    # gate adjudicates its own disagreements under corpus/ANNOTATION_GUIDE.md,
    # so that guide documents adjudication correctly; the claim-review gate
    # reviewer must not be told that a second opinion will settle the record.
    Rule(
        identifier="adjudication-instruction",
        reason="an instruction to adjudicate, to resolve a disagreement, "
        "or to work from both reviewers' decisions",
        roles=CLAIM_GATE_ROLES | {CLAIM_RECORD_ROLE, PACKET_DATA_ROLE, UNEXPECTED_ROLE},
        matcher=_adjudication_instruction,
    ),
    Rule(
        identifier="coordinator-path",
        reason="a coordinator-only local path",
        roles=EVERY_SCANNED_ROLE,
        matcher=_coordinator_path,
    ),
)

RULE_REASONS: tuple[tuple[str, str], ...] = (
    *((rule.identifier, rule.reason) for rule in RULES),
    *PACKET_RULES,
)


def rule_identifiers() -> tuple[str, ...]:
    """Return every rule identifier this module can report."""
    return tuple(identifier for identifier, _ in RULE_REASONS)


def rule_reason(identifier: str) -> str:
    """Return the documented reason one rule exists."""
    for candidate, reason in RULE_REASONS:
        if candidate == identifier:
            return reason
    raise ReviewMaterialsError(f"unknown rule identifier: {identifier}")


def packet_scan_role(name: str) -> str:
    """Return the scan role of one packet entry; anything unlisted is unexpected."""
    for candidate, role in PACKET_SCAN_ROLES:
        if candidate == name:
            return role
    return UNEXPECTED_ROLE


def logical_lines(text: str) -> tuple[tuple[int, str], ...]:
    """Join backslash continuations so a split shell command stays one scanned unit."""
    physical = text.splitlines()
    joined: list[tuple[int, str]] = []
    index = 0
    while index < len(physical):
        start = index
        body = physical[index]
        while body.endswith("\\") and index + 1 < len(physical):
            index += 1
            body = f"{body[:-1].rstrip()} {physical[index].lstrip()}"
        joined.append((start + 1, body))
        index += 1
    return tuple(joined)


def scan_text(text: str, role: str) -> tuple[tuple[int, str, str], ...]:
    """Return every (line, rule, matched text) this role's rules find in *text*."""
    numbered = logical_lines(text)
    bodies = [body for _, body in numbered]
    findings: list[tuple[int, str, str]] = []
    for index, (number, _) in enumerate(numbered):
        for rule in RULES:
            if role not in rule.roles:
                continue
            matched = rule.matcher(bodies, index, role)
            if matched is not None:
                findings.append((number, rule.identifier, matched))
    return tuple(findings)


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReviewMaterialsError(f"cannot read {path}: {exc}") from exc


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # A leak scanner never skips content it cannot decode strictly.
        return data.decode("latin-1")


def scan_file(path: Path, display: str, role: str) -> tuple[Finding, ...]:
    """Scan one file under one role."""
    text = _decode(_read_bytes(path))
    return tuple(
        Finding(display, line, rule, matched) for line, rule, matched in scan_text(text, role)
    )


def scan_root(root: Path) -> tuple[tuple[Finding, ...], int]:
    """Scan the tracked reviewer-facing documents under *root*."""
    findings: list[Finding] = []
    for relative, role in ROOT_REVIEWER_SOURCES:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ReviewMaterialsError(f"reviewer-facing document is missing: {path}")
        findings.extend(scan_file(path, path.as_posix(), role))
    return tuple(findings), len(ROOT_REVIEWER_SOURCES)


def coordinator_document_marker(relative: str) -> str | None:
    """Return why *relative* names material a packet must never carry."""
    lowered = relative.lower()
    for marker, reason in COORDINATOR_DOCUMENT_MARKERS:
        if marker in lowered:
            return f"{marker} ({reason})"
    found = _CANDIDATE_RUN_RE.search(relative)
    return f"{found.group(0)} (a candidate run artifact)" if found is not None else None


def _manifest_role_findings(packet: Path) -> tuple[Finding, ...]:
    path = packet / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise ReviewMaterialsError(f"packet has no {MANIFEST_NAME}: {packet}")
    data = _read_bytes(path)
    try:
        payload = cast(dict[str, Any], load_json_object_bytes(data, str(path)))
    except RunContractError as exc:
        raise ReviewMaterialsError(f"cannot read the packet manifest: {exc}") from exc
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise ReviewMaterialsError(f"packet manifest records no files: {path}")
    lines = _decode(data).splitlines()
    findings: list[Finding] = []
    for entry in entries:
        role = entry.get("role") if isinstance(entry, dict) else None
        if isinstance(role, str) and role in MANIFEST_ROLE_ALLOWLIST:
            continue
        shown = role if isinstance(role, str) else repr(role)
        findings.append(
            Finding(path.as_posix(), _locate(lines, shown), UNLISTED_MANIFEST_ROLE_RULE, shown)
        )
    return tuple(findings)


def _locate(lines: Sequence[str], needle: str) -> int:
    for number, line in enumerate(lines, 1):
        if needle in line:
            return number
    return 1


def scan_packet(packet: Path) -> tuple[tuple[Finding, ...], int]:
    """Scan every entry of a generated packet and its declared manifest roles."""
    if packet.is_symlink() or not packet.is_dir():
        raise ReviewMaterialsError(f"packet is not a directory: {packet}")
    try:
        # Order by the segments of the relative POSIX path, not by the Path
        # objects themselves: Path comparison casefolds on Windows and does
        # not on POSIX, so sorting Path objects made the printed order depend
        # on the host. Same precedent as run_validation.py and model.py.
        entries = sorted(
            packet.rglob("*"),
            key=lambda path: PurePosixPath(path.relative_to(packet).as_posix()).parts,
        )
    except OSError as exc:
        raise ReviewMaterialsError(f"cannot read packet {packet}: {exc}") from exc
    findings: list[Finding] = []
    scanned = 0
    for entry in entries:
        relative = entry.relative_to(packet).as_posix()
        display = entry.as_posix()
        marker = coordinator_document_marker(relative)
        if marker is not None:
            findings.append(Finding(display, 1, COORDINATOR_DOCUMENT_RULE, marker))
        if entry.is_dir():
            continue
        findings.extend(scan_file(entry, display, packet_scan_role(entry.name)))
        scanned += 1
    findings.extend(_manifest_role_findings(packet))
    return tuple(findings), scanned


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser(
        "check", help="scan reviewer-facing material for coordinator-only content"
    )
    check_parser.add_argument(
        "--root", type=Path, default=None, help="tree holding the tracked reviewer documents"
    )
    check_parser.add_argument(
        "--packet",
        type=Path,
        action="append",
        default=[],
        help="generated packet directory; may be repeated",
    )
    args = parser.parse_args(argv)

    packets: list[Path] = list(args.packet)
    root: Path | None = args.root if args.root is not None else (None if packets else Path("."))

    findings: list[Finding] = []
    scanned = 0
    try:
        if root is not None:
            root_findings, root_scanned = scan_root(root)
            findings.extend(root_findings)
            scanned += root_scanned
        for packet in packets:
            packet_findings, packet_scanned = scan_packet(packet)
            findings.extend(packet_findings)
            scanned += packet_scanned
    except ReviewMaterialsError as exc:
        print(f"cannot check review materials: {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(finding.render())
    for identifier in dict.fromkeys(finding.rule for finding in findings):
        print(f"rule {identifier}: {rule_reason(identifier)}")
    print(f"{_plural(len(findings), 'finding')} in {_plural(scanned, 'scanned file')}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
