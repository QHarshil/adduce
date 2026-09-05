"""Reviewer-facing material must never carry coordinator-only information."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, NamedTuple, cast

import pytest
from corpus.scripts import reviewer_packet
from corpus.scripts.check_review_materials import (
    COORDINATOR_DOCUMENT_RULE,
    COORDINATOR_ONLY_ROLE,
    EFFECTIVENESS_METRICS_NAME,
    FINDING_GUIDE_ROLE,
    GUIDE_ROLE,
    MANIFEST_ROLE_ALLOWLIST,
    RULE_REASONS,
    RULES,
    UNLISTED_MANIFEST_ROLE_RULE,
    main,
    rule_identifiers,
    scan_packet,
    scan_text,
)
from corpus.scripts.run_contract import sha256_file, write_json

from tests import review_fixtures

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
REVIEW = CORPUS / "review"
TRACKED_DOCUMENTS = (
    reviewer_packet.GUIDE_NAME,
    reviewer_packet.CHECKLIST_NAME,
    "FINDING_REVIEW_GUIDE.md",
)

FORBIDDEN_LINES = {
    "answer-map": "Claim one resolves R U N R R N N U R R over its ten links.",
    "resolution-aggregate": "Across the record: resolved 24, unresolved 31, not_applicable 45.",
    "uniform-trail-status": "All ten claims carry the same expected_trail_status.",
    "withheld-report": "Read corpus/reports/pilot-claim-links-r2-a.json before you start.",
    "coordinator-command": "Then run claim_review.py merge over both returned files.",
    "candidate-execution": "The candidate pair under test is pilot-0.1.2-r6-a.",
    "facts-appendix": "## Facts appendix",
    "adjudication-instruction": "A third reviewer adjudicates any disagreement you leave.",
    "coordinator-path": "The coordinator runbook is .coordinator/gate-2-runbook.md.",
}
ALLOWED_LINES = (
    "A link correctly recorded as unresolved is decided `verified`, not `revision_required`.",
    "When a link records that the artifact is not resolvable at the pinned commit, and that"
    " is true, the correct decision is `verified`.",
    "The `configuration` link names one artifact and the decision is `unclear`.",
    "Ten claims, and for each claim one claim-level decision plus ten link-level decisions.",
    "Hand completed-review.json to the coordinator and to nobody else.",
    "The pinned clone is at corpus/clones/pilot-2026-07-13/<repo_id>.",
    "Every `unclear` decision names the evidence that is missing or in conflict.",
    "The repository ships .adduce/manifest.yaml and .github/workflows/ci.yml.",
)
ADJUDICATION_SENTENCE = (
    "A disagreement in correctness, applicability, or utility is resolved by an independent"
    " adjudicator who was not an initial reviewer, working from both reviewers' decisions."
)

_FINDING_RE = re.compile(r"^(?P<path>.+):(?P<line>\d+): (?P<rule>[a-z-]+): (?P<matched>.*)$")


class Reported(NamedTuple):
    path: str
    line: int
    rule: str
    matched: str


def parse(output: str) -> list[Reported]:
    """Parse the finding lines out of one run's stdout."""
    findings = []
    for line in output.splitlines():
        if line.startswith("rule ") or not line or line[0].isdigit():
            continue
        found = _FINDING_RE.match(line)
        assert found is not None, f"unparsable finding line: {line}"
        findings.append(
            Reported(found["path"], int(found["line"]), found["rule"], found["matched"])
        )
    return findings


def reviewer_root(tmp_path: Path, *appended: str) -> tuple[Path, int]:
    """Copy the tracked reviewer documents, appending lines to the claim-review guide."""
    root = tmp_path / "tree"
    review = root / "corpus" / "review"
    review.mkdir(parents=True)
    for name in TRACKED_DOCUMENTS:
        shutil.copy(REVIEW / name, review / name)
    guide = review / reviewer_packet.GUIDE_NAME
    original = guide.read_text(encoding="utf-8")
    if appended:
        guide.write_text(original + "\n".join(appended) + "\n", encoding="utf-8")
    return root, len(original.splitlines()) + 1


def packet_manifest(packet: Path) -> dict[str, Any]:
    files = []
    for name, role in reviewer_packet.PACKET_ROLES:
        data = (packet / name).read_bytes()
        files.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "role": role,
            }
        )
    scaffold = json.loads(
        (packet / reviewer_packet.SCAFFOLD_NAME).read_text(encoding="utf-8")
    )
    return {
        "packet_schema_version": reviewer_packet.PACKET_SCHEMA_VERSION,
        "packet_id": "packet-" + "0" * 32,
        "reviewer_id": review_fixtures.REVIEWER_ID,
        "created_at": "2026-08-04T12:00:00+00:00",
        "source_commit": None,
        "claim_ground_truth_sha256": sha256_file(packet / reviewer_packet.CLAIMS_NAME),
        "corpus_inventory_sha256": scaffold["corpus_inventory_sha256"],
        "candidate_pair": scaffold["candidate_pair"],
        "review_scaffold_sha256": sha256_file(packet / reviewer_packet.SCAFFOLD_NAME),
        "clone_root": "corpus/clones/pilot-2026-07-13",
        "repositories": [],
        "files": sorted(files, key=lambda entry: entry["path"]),
        "excluded_material_classes": list(reviewer_packet.EXCLUDED_MATERIAL_CLASSES),
    }


def build_packet(tmp_path: Path, name: str = "packet") -> Path:
    """Assemble a packet by hand from the real reviewer documents and schemas.

    `reviewer_packet.build_packet` resolves every corpus repository against the
    pinned clone root, which is gitignored and absent wherever the suite runs
    unattended, so the packet is assembled here from the same allowlist of
    roles rather than through the builder.
    """
    packet = tmp_path / name
    packet.mkdir(parents=True)
    truth = review_fixtures.synthetic_truth(2)
    truth_path = packet / reviewer_packet.CLAIMS_NAME
    write_json(truth_path, truth)
    write_json(
        packet / reviewer_packet.SCAFFOLD_NAME,
        review_fixtures.scaffold_for(truth_path, truth),
    )
    for name_ in (reviewer_packet.GUIDE_NAME, reviewer_packet.CHECKLIST_NAME):
        shutil.copy(REVIEW / name_, packet / name_)
    for name_ in (reviewer_packet.REVIEW_SCHEMA_NAME, reviewer_packet.WORKSPACE_SCHEMA_NAME):
        shutil.copy(CORPUS / name_, packet / name_)
    (packet / reviewer_packet.VERIFY_WRAPPER_NAME).write_text(
        reviewer_packet.VERIFY_WRAPPER_SOURCE, encoding="utf-8"
    )
    write_json(packet / reviewer_packet.MANIFEST_NAME, packet_manifest(packet))
    return packet


def test_the_tracked_reviewer_documents_carry_no_coordinator_material(capsys):
    assert main(["check", "--root", str(ROOT)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 findings in 3 scanned files"


def test_the_default_root_is_the_working_directory(tmp_path, monkeypatch, capsys):
    root, _ = reviewer_root(tmp_path)
    monkeypatch.chdir(root)
    assert main(["check"]) == 0
    assert "0 findings in 3 scanned files" in capsys.readouterr().out


@pytest.mark.parametrize("rule", rule_identifiers())
def test_every_rule_fires_on_a_synthetic_forbidden_document(rule, tmp_path, capsys):
    if rule == COORDINATOR_DOCUMENT_RULE:
        packet = build_packet(tmp_path)
        shutil.copy(REVIEW / EFFECTIVENESS_METRICS_NAME, packet / EFFECTIVENESS_METRICS_NAME)
        argv = ["check", "--packet", str(packet)]
        expected_line, expected_name = 1, EFFECTIVENESS_METRICS_NAME
    elif rule == UNLISTED_MANIFEST_ROLE_RULE:
        packet = build_packet(tmp_path)
        manifest = packet_manifest(packet)
        manifest["files"][0]["role"] = "coordinator_answer_summary"
        write_json(packet / reviewer_packet.MANIFEST_NAME, manifest)
        argv = ["check", "--packet", str(packet)]
        expected_line, expected_name = None, reviewer_packet.MANIFEST_NAME
    else:
        root, line = reviewer_root(tmp_path, FORBIDDEN_LINES[rule])
        argv = ["check", "--root", str(root)]
        expected_line, expected_name = line, reviewer_packet.GUIDE_NAME

    assert main(argv) == 1
    reported = [found for found in parse(capsys.readouterr().out) if found.rule == rule]
    assert reported, f"{rule} did not fire"
    assert all(Path(found.path).name == expected_name for found in reported)
    if expected_line is not None:
        assert [found.line for found in reported] == [expected_line]


@pytest.mark.parametrize("allowed", ALLOWED_LINES)
def test_ordinary_reviewer_prose_fires_nothing(allowed, tmp_path, capsys):
    root, _ = reviewer_root(tmp_path, allowed)
    assert main(["check", "--root", str(root)]) == 0
    assert parse(capsys.readouterr().out) == []


def test_adjudication_instruction_is_scoped_to_the_claim_review_gate():
    assert [rule for _, rule, _ in scan_text(ADJUDICATION_SENTENCE, FINDING_GUIDE_ROLE)] == []
    fired = [rule for _, rule, _ in scan_text(ADJUDICATION_SENTENCE, GUIDE_ROLE)]
    assert fired == ["adjudication-instruction"]


def test_the_tracked_finding_review_guide_documents_its_own_adjudication():
    text = (REVIEW / "FINDING_REVIEW_GUIDE.md").read_text(encoding="utf-8")
    assert "adjudicator" in text
    assert scan_text(text, FINDING_GUIDE_ROLE) == ()
    assert [rule for _, rule, _ in scan_text(text, GUIDE_ROLE)] != []


def test_the_decision_vocabulary_does_not_read_as_a_resolution_aggregate():
    explanation = (
        "The most-missed rule: `verified` means the record is right. When a link records"
        " that the expected artifact is unresolved at the pinned commit, and that is true,"
        " the correct decision is `verified`."
    )
    assert scan_text(explanation, GUIDE_ROLE) == ()
    aggregate = "Expected resolutions: resolved 24, unresolved 31, not_applicable 45."
    assert [rule for _, rule, _ in scan_text(aggregate, GUIDE_ROLE)] == ["resolution-aggregate"]


def test_a_resolution_distribution_split_over_table_rows_still_fires():
    table = "| resolved | 24 |\n| unresolved | 31 |\n| not_applicable | 45 |\n"
    fired = scan_text(table, GUIDE_ROLE)
    assert {rule for _, rule, _ in fired} == {"resolution-aggregate"}
    assert [line for line, _, _ in fired] == [2, 3]


def test_a_leaked_answer_map_inside_a_fence_still_fires():
    fenced = "```console\nR U N R R N N U R R\n```\n"
    assert [(line, rule) for line, rule, _ in scan_text(fenced, GUIDE_ROLE)] == [(2, "answer-map")]


def test_a_continued_coordinator_command_is_scanned_as_one_line():
    text = "python -B corpus/scripts/claim_review.py \\\n  merge --out merged.json\n"
    assert [(line, rule) for line, rule, _ in scan_text(text, GUIDE_ROLE)] == [
        (1, "coordinator-command")
    ]


def test_effectiveness_metrics_is_exempt_under_root_but_refused_inside_a_packet(
    tmp_path, capsys
):
    metrics = (REVIEW / EFFECTIVENESS_METRICS_NAME).read_text(encoding="utf-8")
    assert scan_text(metrics, GUIDE_ROLE) != ()
    assert main(["check", "--root", str(ROOT)]) == 0
    capsys.readouterr()

    packet = build_packet(tmp_path)
    shutil.copy(REVIEW / EFFECTIVENESS_METRICS_NAME, packet / EFFECTIVENESS_METRICS_NAME)
    assert main(["check", "--packet", str(packet)]) == 1
    reported = parse(capsys.readouterr().out)
    assert COORDINATOR_DOCUMENT_RULE in {found.rule for found in reported}


def test_a_clean_packet_passes(tmp_path, capsys):
    packet = build_packet(tmp_path)
    assert main(["check", "--packet", str(packet)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 findings in 8 scanned files"


def test_a_packet_scan_leaves_the_tracked_documents_unscanned(tmp_path, capsys):
    build_packet(tmp_path)
    assert main(["check", "--packet", str(tmp_path / "packet")]) == 0
    assert "in 8 scanned files" in capsys.readouterr().out


def test_both_roots_and_packets_are_scanned_in_one_run(tmp_path, capsys):
    packet = build_packet(tmp_path)
    assert main(["check", "--root", str(ROOT), "--packet", str(packet)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 findings in 11 scanned files"


def test_an_unexpected_packet_file_is_scanned_as_reviewer_prose(tmp_path, capsys):
    packet = build_packet(tmp_path)
    (packet / "NOTES.md").write_text(
        "Working note.\nClaim one resolves R U N R R N N U R R.\n", encoding="utf-8"
    )
    assert main(["check", "--packet", str(packet)]) == 1
    reported = parse(capsys.readouterr().out)
    assert [(Path(found.path).name, found.line, found.rule) for found in reported] == [
        ("NOTES.md", 2, "answer-map")
    ]


def test_a_manifest_role_outside_the_allowlist_is_reported_at_its_line(tmp_path, capsys):
    packet = build_packet(tmp_path)
    manifest = packet_manifest(packet)
    manifest["files"][0]["role"] = "coordinator_answer_summary"
    write_json(packet / reviewer_packet.MANIFEST_NAME, manifest)
    assert main(["check", "--packet", str(packet)]) == 1
    reported = parse(capsys.readouterr().out)
    assert len(reported) == 1
    found = reported[0]
    assert found.rule == UNLISTED_MANIFEST_ROLE_RULE
    assert found.matched == "coordinator_answer_summary"
    manifest_lines = (packet / reviewer_packet.MANIFEST_NAME).read_text(
        encoding="utf-8"
    ).splitlines()
    assert "coordinator_answer_summary" in manifest_lines[found.line - 1]


def test_the_manifest_role_allowlist_is_the_published_packet_schema():
    schema = json.loads(
        (CORPUS / "reviewer-packet.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["$defs"]["role"]["enum"]) == set(MANIFEST_ROLE_ALLOWLIST)


def test_a_missing_reviewer_document_is_unusable_input(tmp_path, capsys):
    root, _ = reviewer_root(tmp_path)
    (root / "corpus" / "review" / reviewer_packet.CHECKLIST_NAME).unlink()
    assert main(["check", "--root", str(root)]) == 2
    assert "reviewer-facing document is missing" in capsys.readouterr().err


def test_a_packet_without_a_manifest_is_unusable_input(tmp_path, capsys):
    packet = build_packet(tmp_path)
    (packet / reviewer_packet.MANIFEST_NAME).unlink()
    assert main(["check", "--packet", str(packet)]) == 2
    assert reviewer_packet.MANIFEST_NAME in capsys.readouterr().err


def test_a_packet_path_that_is_not_a_directory_is_unusable_input(tmp_path, capsys):
    assert main(["check", "--packet", str(tmp_path / "absent")]) == 2
    assert "packet is not a directory" in capsys.readouterr().err


def test_a_finding_names_the_file_the_line_the_rule_and_the_match(tmp_path, capsys):
    root, line = reviewer_root(tmp_path, "", FORBIDDEN_LINES["answer-map"])
    assert main(["check", "--root", str(root)]) == 1
    output = capsys.readouterr().out
    reported = parse(output)
    assert len(reported) == 1
    found = reported[0]
    assert found.path.endswith(f"corpus/review/{reviewer_packet.GUIDE_NAME}")
    assert found.line == line + 1
    assert found.rule == "answer-map"
    assert "R U N R R N N U R R" in found.matched
    assert "rule answer-map: a compact expected-resolution map" in output
    assert output.splitlines()[-1] == "1 finding in 3 scanned files"


def test_every_reported_rule_has_a_documented_reason():
    identifiers = rule_identifiers()
    assert len(identifiers) == len(set(identifiers))
    assert all(reason and reason[0].islower() for _, reason in RULE_REASONS)


def test_no_rule_is_scoped_to_the_coordinator_only_role():
    assert RULES
    assert all(COORDINATOR_ONLY_ROLE not in rule.roles for rule in RULES)

ANSWER_MAP_NOTE = "Working note.\nClaim one resolves R U N R R N N U R R.\n"
#: Names that separate the two candidate ordering rules. The uppercase leading
#: name divides the POSIX and Windows flavours, which casefold differently.
FLAVOUR_SENSITIVE_NOTES = ("NOTES.md", "notes-b.md")


def write_flavour_sensitive_notes(packet: Path) -> None:
    """Add scannable notes whose order differs between ordering flavours."""
    for relative in FLAVOUR_SENSITIVE_NOTES:
        path = packet / relative
        path.write_text(ANSWER_MAP_NOTE, encoding="utf-8")


def reported_order(packet: Path, scanned: Path | None = None) -> list[str]:
    """The relative paths the packet's findings are reported in."""
    findings, _ = scan_packet(scanned if scanned is not None else packet)
    return [Path(found.path).relative_to(packet).as_posix() for found in findings]


class WindowsOrderedPath:
    """A real path that sorts the way it would on Windows, so the casefolding
    condition can be reproduced on a POSIX CI runner. Borrowed from #49,
    which introduced it for this same defect."""

    def __init__(self, real: Path, root: Path) -> None:
        self._real = real
        self._root = root

    def __lt__(self, other: "WindowsOrderedPath") -> bool:
        return PureWindowsPath(self._real.relative_to(self._root)) < PureWindowsPath(
            other._real.relative_to(other._root)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def __truediv__(self, other: str) -> Path:
        return self._real / other

    def relative_to(self, other: object) -> Path:
        return self._real.relative_to(self._root)

    def rglob(self, pattern: str) -> "Iterator[WindowsOrderedPath]":
        for path in self._real.rglob(pattern):
            yield WindowsOrderedPath(path, self._root)


def test_packet_findings_are_ordered_by_posix_segments_not_by_host_flavour(tmp_path):
    """Reproduces the Windows condition (Path comparison casefolds) on any
    platform, so the ordering contract is exercised where CI can see it."""
    packet = build_packet(tmp_path)
    write_flavour_sensitive_notes(packet)
    posix_order = sorted(FLAVOUR_SENSITIVE_NOTES, key=PurePosixPath)
    windows_order = sorted(FLAVOUR_SENSITIVE_NOTES, key=PureWindowsPath)

    # Guards the test against going vacuous: the assertions below only say
    # something while the two flavours still order these names differently.
    assert posix_order != windows_order

    windows_host = cast(Path, WindowsOrderedPath(packet, packet))
    assert reported_order(packet, windows_host) == posix_order
    assert reported_order(packet) == posix_order


def test_scan_packet_orders_by_posix_segments(tmp_path):
    packet = build_packet(tmp_path)
    (packet / "notes-x.md").write_text(ANSWER_MAP_NOTE, encoding="utf-8")
    (packet / "notes" / "inner.md").parent.mkdir(parents=True)
    (packet / "notes" / "inner.md").write_text(ANSWER_MAP_NOTE, encoding="utf-8")
    entries = scan_packet(packet)[0]
    relative = [entry.path for entry in entries]
    assert relative == sorted(relative, key=lambda p: PurePosixPath(p).parts)