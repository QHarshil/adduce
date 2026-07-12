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


def test_sarif_excludes_passes_and_suppressed(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    sarif = json.loads(RENDERERS["sarif"](result))
    reported = {r["ruleId"] for r in sarif["runs"][0]["results"]}
    passed = {f.rule_id for f in result.card.findings if f.status.value == "pass"}
    assert not (reported & passed)


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
