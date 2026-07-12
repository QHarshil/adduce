"""Generation-safety layer: evidence ledger, strict drafting, audit-generated, package."""

from __future__ import annotations

import hashlib
import json
import subprocess

import yaml
from typer.testing import CliRunner

from adduce import __version__
from adduce.checklists import load_checklist, render_markdown
from adduce.cli import app
from adduce.engine import run_check
from adduce.ledger import AnswerLevel, EvidenceStrength, derive_answer, load_ledger
from adduce.report import appendix as appendix_report
from adduce.rules.base import Category, Finding, Location, Status
from tests.conftest import plain
from tests.test_engine import BARE, WELL_FORMED, _write

# Wide columns keep phrases on one line; plain() strips color codes.
runner = CliRunner(env={"COLUMNS": "300"})


def _finding(status, confidence, rule_id="R-DOC-001", locations=None):
    return Finding(
        rule_id=rule_id,
        category=Category.DOCUMENTATION,
        title="t",
        status=status,
        confidence=confidence,
        message=f"{rule_id} is {status.value}",
        remediation="",
        weight=1,
        locations=locations or [],
    )


def _git(tmp_path, *args):
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


# -- derive_answer policy ----------------------------------------------------


def test_derive_answer_default_yes_boundary_at_085():
    loc = [Location("train.py", 1)]
    answer, evidence = derive_answer([_finding(Status.PASS, 0.85, locations=loc)], False)
    assert answer is AnswerLevel.YES
    assert evidence and evidence[0].strength is EvidenceStrength.DIRECT
    answer, _ = derive_answer([_finding(Status.PASS, 0.84, locations=loc)], False)
    assert answer is AnswerLevel.PARTIAL


def test_derive_answer_manifest_backing_substitutes_for_confidence():
    answer, evidence = derive_answer([_finding(Status.PASS, 0.6)], True)
    assert answer is AnswerLevel.YES
    assert any(
        item.kind == "manifest" and item.strength is EvidenceStrength.MANIFEST_AUTHOR_CONFIRMED
        for item in evidence
    )


def test_derive_answer_inferred_pass_cannot_become_yes():
    answer, evidence = derive_answer([_finding(Status.PASS, 0.99)], False)
    assert answer is AnswerLevel.PARTIAL
    assert evidence and all(item.strength is EvidenceStrength.INFERRED for item in evidence)


def test_derive_answer_partial_lower_boundary_at_050():
    answer, _ = derive_answer([_finding(Status.PASS, 0.50)], False)
    assert answer is AnswerLevel.PARTIAL
    answer, _ = derive_answer([_finding(Status.PASS, 0.49)], False)
    assert answer is AnswerLevel.UNKNOWN


def test_derive_answer_mixed_not_detected_and_unknown():
    mixed = [_finding(Status.PASS, 0.95), _finding(Status.FAIL, 0.95, rule_id="R-DOC-002")]
    assert derive_answer(mixed, False)[0] is AnswerLevel.PARTIAL
    all_fail = [_finding(Status.FAIL, 0.9), _finding(Status.FAIL, 0.9, rule_id="R-DOC-002")]
    assert derive_answer(all_fail, False)[0] is AnswerLevel.NOT_DETECTED
    unscored = [_finding(Status.NOT_APPLICABLE, 0.9)]
    assert derive_answer(unscored, False)[0] is AnswerLevel.UNKNOWN
    assert derive_answer([], False)[0] is AnswerLevel.UNKNOWN


def test_derive_answer_strict_yes_boundary_at_090():
    loc = [Location("train.py", 1)]
    answer, _ = derive_answer([_finding(Status.PASS, 0.90, locations=loc)], False, strict=True)
    assert answer is AnswerLevel.YES
    answer, _ = derive_answer([_finding(Status.PASS, 0.89, locations=loc)], False, strict=True)
    assert answer is AnswerLevel.PARTIAL


def test_derive_answer_strict_inferred_only_goes_back_to_author():
    # High confidence but no source locations: inferred-only evidence.
    findings = [_finding(Status.PASS, 0.95)]
    assert derive_answer(findings, False)[0] is AnswerLevel.PARTIAL
    answer, evidence = derive_answer(findings, False, strict=True)
    assert answer is AnswerLevel.AUTHOR_INPUT_REQUIRED
    assert all(item.strength is EvidenceStrength.INFERRED for item in evidence)
    # Manifest backing is author-confirmed, so strict does not bounce it back.
    assert derive_answer(findings, True, strict=True)[0] is AnswerLevel.YES


def test_draft_manifest_claim_does_not_back_unrelated_checklist_items(tmp_path):
    _write(tmp_path, WELL_FORMED)
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "adduce/1",
                "claims": [
                    {
                        "id": "C1",
                        "status": "draft",
                        "metric": "accuracy",
                        "value": 92.1,
                        "produced_by": {"command": "bash run.sh"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_check(tmp_path)
    _, ledger = render_markdown(load_checklist("neurips"), result)
    assert all(
        not any(item.strength is EvidenceStrength.MANIFEST_AUTHOR_CONFIRMED for item in entry.evidence)
        for entry in ledger.entries
    )


# -- checklist command: ledger, markers, anchors, summary ---------------------


def test_checklist_command_writes_ledger_with_provenance(tmp_path):
    _write(tmp_path, WELL_FORMED)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    target = tmp_path / "checklist.md"
    result = runner.invoke(app, ["checklist", str(tmp_path), "--output", str(target)])
    assert result.exit_code == 0, result.output
    records = load_ledger(tmp_path)
    assert "checklist.md" in records
    record = records["checklist.md"]
    assert record["generated_text_policy"] == "evidence_only"
    provenance = record["provenance"]
    assert provenance["adduce_version"] == __version__
    assert provenance["command"] == "checklist"
    assert provenance["profile"] == "neurips"
    assert provenance["repo_commit"]
    assert provenance["generated_at"]
    assert record["artifact_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_ledger_holds_multiple_artifacts(tmp_path):
    _write(tmp_path, WELL_FORMED)
    checklist_out = tmp_path / "checklist.md"
    appendix_out = tmp_path / "appendix.md"
    assert runner.invoke(app, ["checklist", str(tmp_path), "-o", str(checklist_out)]).exit_code == 0
    assert runner.invoke(app, ["appendix", str(tmp_path), "-o", str(appendix_out)]).exit_code == 0
    records = load_ledger(tmp_path)
    assert {"checklist.md", "appendix.md"} <= set(records)
    appendix_items = {entry["item_id"] for entry in records["appendix.md"]["entries"]}
    assert {"A.2", "A.6"} <= appendix_items


def test_acl_manual_item_marked_for_author_review(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    markdown, ledger = render_markdown(load_checklist("acl"), result)
    assert "[AUTHOR REVIEW REQUIRED] — depends on information outside the repository" in markdown
    entry = next(e for e in ledger.entries if e.item_id == "artifacts-cited")
    assert entry.answer is AnswerLevel.AUTHOR_INPUT_REQUIRED
    assert entry.evidence == []
    assert "R-LIC-002" in entry.searched


def test_evidence_anchors_appear(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    files = dict(BARE)
    files["model.py"] = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(None, shuffle=True)\n"
    )
    _write(repo, files)
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        yaml.safe_dump(
            {
                "name": "Custom",
                "key": "custom",
                "items": [
                    {
                        "id": "loader-seeding",
                        "question": "Is shuffling seeded?",
                        "rules": ["R-DET-004"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_check(repo)
    markdown, _ = render_markdown(load_checklist(str(custom)), result)
    assert "[EVIDENCE: model.py:3" in markdown


def test_generation_summary_printed_to_stderr_not_artifact(tmp_path):
    _write(tmp_path, WELL_FORMED)
    target = tmp_path / "checklist.md"
    result = runner.invoke(app, ["checklist", str(tmp_path), "--output", str(target)])
    assert result.exit_code == 0
    output = plain(result.output)
    assert "generation summary:" in output
    assert "evidence-backed" in output
    assert "ledger:" in output
    # WELL_FORMED drafts partial answers, so the review warning must show.
    assert "Review required before submission — this draft is not submission-ready." in output
    # The artifact itself must never carry the summary.
    assert "generation summary" not in target.read_text(encoding="utf-8")


def test_strict_evidence_downgrades_answers(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    _, default_ledger = render_markdown(load_checklist("neurips"), result)
    _, strict_ledger = render_markdown(load_checklist("neurips"), result, strict=True)
    default_answers = {e.item_id: e.answer for e in default_ledger.entries}
    strict_answers = {e.item_id: e.answer for e in strict_ledger.entries}
    downgraded = [
        item_id
        for item_id, answer in default_answers.items()
        if answer in (AnswerLevel.YES, AnswerLevel.PARTIAL)
        and strict_answers[item_id] is AnswerLevel.AUTHOR_INPUT_REQUIRED
    ]
    assert downgraded
    # And through the CLI flag: strict output marks more items for the author.
    default_run = runner.invoke(app, ["checklist", str(tmp_path)])
    strict_run = runner.invoke(app, ["checklist", str(tmp_path), "--strict-evidence"])
    assert plain(strict_run.output).count("[AUTHOR REVIEW REQUIRED]") > plain(
        default_run.output
    ).count("[AUTHOR REVIEW REQUIRED]")


# -- audit-generated ----------------------------------------------------------


def test_audit_generated_passes_fresh_then_flags_edits(tmp_path):
    _write(tmp_path, WELL_FORMED)
    target = tmp_path / "checklist.md"
    assert runner.invoke(app, ["checklist", str(tmp_path), "-o", str(target)]).exit_code == 0
    fresh = runner.invoke(app, ["audit-generated", str(target), str(tmp_path)])
    assert fresh.exit_code == 0, fresh.output
    # A post-generation edit fires R-GEN-005.
    target.write_text(target.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    edited = runner.invoke(app, ["audit-generated", str(target), str(tmp_path)])
    assert edited.exit_code == 1
    assert "R-GEN-005" in plain(edited.output)


def test_audit_generated_flags_execution_claims(tmp_path):
    _write(tmp_path, WELL_FORMED)
    target = tmp_path / "checklist.md"
    assert runner.invoke(app, ["checklist", str(tmp_path), "-o", str(target)]).exit_code == 0
    target.write_text(
        target.read_text(encoding="utf-8") + "\nAll results were reproduced.\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["audit-generated", str(target), str(tmp_path)])
    assert result.exit_code == 1
    assert "R-GEN-003" in plain(result.output)
    assert "dynamic_verified" in plain(result.output)


def test_audit_generated_placeholders_are_informational(tmp_path):
    _write(tmp_path, WELL_FORMED)
    target = tmp_path / "appendix.md"
    assert runner.invoke(app, ["appendix", str(tmp_path), "-o", str(target)]).exit_code == 0
    result = runner.invoke(app, ["audit-generated", str(target), str(tmp_path)])
    # The appendix keeps author-review markers: R-GEN-004 alone exits 0.
    assert result.exit_code == 0, result.output
    assert "R-GEN-004" in plain(result.output)


def test_appendix_does_not_invent_access_installation_or_tolerance(tmp_path):
    _write(tmp_path, WELL_FORMED)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    target = tmp_path / "appendix.md"

    generated = runner.invoke(app, ["appendix", str(tmp_path), "-o", str(target)])

    assert generated.exit_code == 0, generated.output
    text = target.read_text(encoding="utf-8")
    assert "**Publicly available:** Yes" not in text
    assert "Public git repository" not in text
    assert "**Publicly available:** [AUTHOR REVIEW REQUIRED]" in text
    assert "pip install -r requirements.txt" not in text
    assert "a rerun should land within the stated tolerance" not in text
    assert "[AUTHOR REVIEW REQUIRED] State the acceptable tolerance" in text


def test_appendix_ledger_records_manifest_evidence_for_claim_table(tmp_path):
    _write(tmp_path, WELL_FORMED)
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "adduce/1",
                "claims": [
                    {
                        "id": "C1",
                        "status": "confirmed",
                        "metric": "accuracy",
                        "value": 92.1,
                        "produced_by": {"command": "bash run.sh"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    _, ledger = appendix_report.render(run_check(tmp_path))
    a6 = next(entry for entry in ledger.entries if entry.item_id == "A.6")

    assert any(
        item.kind == "manifest" and item.path == ".adduce/manifest.yaml"
        for item in a6.evidence
    )


def test_audit_generated_without_ledger_errors(tmp_path):
    _write(tmp_path, WELL_FORMED)
    artifact = tmp_path / "checklist.md"
    artifact.write_text("# stray artifact\n", encoding="utf-8")
    result = runner.invoke(app, ["audit-generated", str(artifact), str(tmp_path)])
    assert result.exit_code == 2
    assert "evidence ledger" in plain(result.output)


# -- package ------------------------------------------------------------------


def test_package_produces_bundle_and_refuses_rerun(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = runner.invoke(app, ["package", str(tmp_path), "--profile", "neurips"])
    assert result.exit_code == 0, result.output
    package_dir = tmp_path / "adduce-submission"
    for name in (
        "checklist.md",
        "artifact_appendix.md",
        "manifest.yaml",
        "evidence-ledger.json",
        "checksums.txt",
        "citation.cff",
        "ro-crate-metadata.json",
    ):
        assert (package_dir / name).is_file(), name
    records = json.loads((package_dir / "evidence-ledger.json").read_text(encoding="utf-8"))
    assert {"checklist.md", "artifact_appendix.md"} <= set(records)
    output = plain(result.output)
    assert "generation summary:" in output
    assert "Every file is a draft" in output
    # The package never touches the repository's own .adduce/ manifest.
    assert not (tmp_path / ".adduce" / "manifest.yaml").exists()
    # The bundled ledger is auditable in place.
    audit = runner.invoke(
        app, ["audit-generated", str(package_dir / "checklist.md"), str(tmp_path)]
    )
    assert audit.exit_code == 0, audit.output
    # Refuses to overwrite without --force, then obeys it.
    rerun = runner.invoke(app, ["package", str(tmp_path)])
    assert rerun.exit_code == 2
    assert "--force" in plain(rerun.output)
    forced = runner.invoke(app, ["package", str(tmp_path), "--force"])
    assert forced.exit_code == 0, forced.output
