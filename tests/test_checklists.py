"""Checklist loading and drafting."""

from __future__ import annotations

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


def test_good_repo_drafts_yes_answers(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    output = render_markdown(load_checklist("neurips"), result)
    assert "Yes (draft)" in output
    assert "Verify each answer" in output


def test_manual_items_stay_manual(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    output = render_markdown(load_checklist("acl"), result)
    assert "requires author input" in output


def test_bare_repo_drafts_negative_answers(tmp_path):
    _write(tmp_path, BARE)
    result = run_check(tmp_path)
    output = render_markdown(load_checklist("neurips"), result)
    assert "No (draft)" in output or "Partial (draft)" in output
