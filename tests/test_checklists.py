"""Checklist loading and drafting."""

from __future__ import annotations

import yaml

from adduce.checklists import available_checklists, load_checklist, render_markdown
from adduce.engine import run_check
from tests.test_engine import BARE, WELL_FORMED, _write


def test_bundled_checklists_load():
    names = available_checklists()
    assert {"neurips", "acl"} <= set(names)
    for name in names:
        checklist = load_checklist(name)
        assert checklist.items, name
        for item in checklist.items:
            assert item.question.endswith("?")


def test_checklist_rule_ids_exist():
    from adduce.rules import discover_rules

    known = {rule.id for rule in discover_rules()}
    for name in available_checklists():
        for item in load_checklist(name).items:
            missing = set(item.rules) - known
            assert not missing, f"{name}/{item.id} references unknown rules: {missing}"


def test_manifest_claim_does_not_back_unrelated_answers(tmp_path):
    _write(tmp_path, WELL_FORMED)
    # A metric claim must not lift unrelated documentation, data, or licensing
    # questions to a drafted yes.
    (tmp_path / ".adduce").mkdir()
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "adduce/1",
                "claims": [{"id": "C1", "metric": "accuracy", "value": 92.1}],
            }
        ),
        encoding="utf-8",
    )
    result = run_check(tmp_path)
    output, _ = render_markdown(load_checklist("neurips"), result)
    assert "Yes (draft)" not in output
    assert "Partial (draft)" in output
    assert "Verify each answer" in output


def test_manual_items_stay_manual(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    output, _ = render_markdown(load_checklist("acl"), result)
    assert "[AUTHOR REVIEW REQUIRED]" in output
    assert "depends on information outside the repository" in output


def test_bare_repo_drafts_negative_answers(tmp_path):
    _write(tmp_path, BARE)
    result = run_check(tmp_path)
    output, _ = render_markdown(load_checklist("neurips"), result)
    assert "Not detected (draft)" in output or "Partial (draft)" in output
