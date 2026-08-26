"""Report renderers: structural validity of each output format."""

from __future__ import annotations

import json

from adduce.engine import run_check
from adduce.report import RENDERERS, codemeta, software_heritage
from tests.test_engine import BARE, WELL_FORMED, _write


def test_all_renderers_produce_output(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    for name, renderer in RENDERERS.items():
        output = renderer(result)
        assert output.strip(), name


def test_sarif_structure(tmp_path):
    _write(tmp_path, BARE)
    result = run_check(tmp_path)
    sarif = json.loads(RENDERERS["sarif"](result))
    run = sarif["runs"][0]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    for item in run["results"]:
        assert item["ruleId"] in rule_ids
        assert item["level"] in {"note", "warning", "error"}
        assert item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert "partialFingerprints" in item


def test_sarif_excludes_passes_but_retains_suppressed_findings(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = 'ignore = ["R-LIC-001"]\n'
    _write(tmp_path, files)
    result = run_check(tmp_path)
    sarif = json.loads(RENDERERS["sarif"](result))
    results = sarif["runs"][0]["results"]
    reported = {r["ruleId"] for r in results}
    passed = {f.rule_id for f in result.card.findings if f.status.value == "pass"}
    assert not (reported & passed)
    suppressed = next(item for item in results if item["ruleId"] == "R-LIC-001")
    assert suppressed["suppressions"][0]["status"] == "accepted"


def test_markdown_contains_score_and_disclaimer(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    output = RENDERERS["markdown"](result)
    assert "Reproducibility report" in output
    assert "not a certification" in output


def test_latex_escapes_special_characters(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    output = RENDERERS["latex"](result)
    assert r"\section*{Reproducibility}" in output
    for line in output.splitlines():
        if line.startswith(r"  \item"):
            assert "_" not in line.replace(r"\_", ""), line


def test_badge_color_tracks_score(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"
    good_root.mkdir()
    bad_root.mkdir()
    _write(good_root, WELL_FORMED)
    _write(bad_root, BARE)
    good_badge = json.loads(RENDERERS["badge"](run_check(good_root)))
    bad_badge = json.loads(RENDERERS["badge"](run_check(bad_root)))
    assert good_badge["color"] in {"brightgreen", "green"}
    assert bad_badge["color"] in {"yellow", "orange"}


def test_repository_exports_strip_remote_credentials(tmp_path):
    import subprocess

    _write(tmp_path, WELL_FORMED)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    secret = "ghp_" + "a" * 36
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            f"https://{secret}@github.com/example/project.git?token=also-secret",
        ],
        cwd=tmp_path,
        check=True,
    )
    result = run_check(tmp_path)

    codemeta_doc = json.loads(codemeta.render(result))
    heritage_note = software_heritage.render(result)

    assert codemeta_doc["codeRepository"] == "https://github.com/example/project.git"
    assert "https://github.com/example/project.git" in heritage_note
    assert secret not in heritage_note
    assert "also-secret" not in heritage_note


def _terminal_text(result, findings):
    """Render the terminal report over a constructed score card."""
    from rich.console import Console

    from adduce.profiles import load_profile
    from adduce.report import terminal
    from adduce.scoring import score

    result.card = score(findings, load_profile("default"))
    console = Console(width=200, record=True, force_terminal=False, legacy_windows=False)
    terminal.render(result, console)
    return console.export_text()


def _category_finding(rule_id, category, status, weight=3):
    from adduce.rules.base import Finding

    return Finding(
        rule_id=rule_id,
        category=category,
        title=rule_id,
        status=status,
        confidence=0.8,
        message=f"{rule_id} message",
        remediation="",
        weight=weight,
    )


def test_a_category_holding_pass_and_unknown_does_not_claim_everything_is_satisfied(tmp_path):
    from adduce.rules.base import Category, Status

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    text = _terminal_text(
        result,
        [
            _category_finding("A", Category.CODE_EXECUTION, Status.PASS),
            _category_finding("B", Category.CODE_EXECUTION, Status.UNKNOWN),
        ],
    )
    assert "all detected checks satisfied" not in text
    assert "could not be assessed" in text


def test_a_category_with_nothing_unknown_still_reads_as_satisfied(tmp_path):
    from adduce.rules.base import Category, Status

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    text = _terminal_text(
        result,
        [_category_finding("A", Category.CODE_EXECUTION, Status.PASS)],
    )
    assert "all detected checks satisfied" in text


def test_reporters_render_a_card_with_no_score_without_printing_a_number(tmp_path):
    """`None` is the absence of a score, so no reporter may render it as zero."""
    from adduce.report import badge
    from adduce.rules.base import Category, Status

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    terminal_text = _terminal_text(
        result,
        [
            _category_finding("A", Category.NOTEBOOK, Status.UNKNOWN),
            _category_finding("B", Category.DATA, Status.NOT_APPLICABLE),
        ],
    )
    assert result.card.total is None

    markdown_text = RENDERERS["markdown"](result)
    badge_payload = json.loads(RENDERERS["badge"](result))
    svg = badge.render_svg(result)

    assert "no score" in terminal_text
    assert "not assessed" in markdown_text
    assert badge_payload["message"] == "not assessed"
    assert badge_payload["color"] == "lightgrey"
    for rendered in (terminal_text, markdown_text, svg):
        assert "0/100" not in rendered


def test_an_all_unknown_category_is_visible_and_shows_no_score(tmp_path):
    from adduce.rules.base import Category, Status

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    text = _terminal_text(
        result,
        [
            _category_finding("A", Category.CODE_EXECUTION, Status.PASS),
            _category_finding("B", Category.NOTEBOOK, Status.UNKNOWN),
            _category_finding("C", Category.NOTEBOOK, Status.UNKNOWN),
        ],
    )
    assert Category.NOTEBOOK.value in text
    assert "none could be assessed" in text
    assert "0/0" not in text
