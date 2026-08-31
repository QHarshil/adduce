"""Report renderers: structural validity of each output format."""

from __future__ import annotations

import json

import pytest

from adduce.engine import run_check
from adduce.report import RENDERERS, codemeta, software_heritage
from adduce.report.badge import render_svg as RENDERERS_SVG
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


def _write_rated(root, files):
    """Write ``files`` plus enough source for the score to carry a tier.

    Both reporter fixtures are a handful of lines, which is below the floor a
    tier is assigned above, so a card built from either is unrated. Colour is a
    statement about a rated score, so a test about colour needs a rated card.
    """
    padded = dict(files)
    padded["extra_module.py"] = "\n".join(f"CONSTANT_{index} = {index}" for index in range(140))
    _write(root, padded)


def test_badge_color_tracks_score(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"
    good_root.mkdir()
    bad_root.mkdir()
    _write_rated(good_root, WELL_FORMED)
    _write_rated(bad_root, BARE)
    good_result = run_check(good_root)
    bad_result = run_check(bad_root)
    assert good_result.card.rated and bad_result.card.rated
    good_badge = json.loads(RENDERERS["badge"](good_result))
    bad_badge = json.loads(RENDERERS["badge"](bad_result))
    assert good_badge["color"] in {"brightgreen", "green"}
    assert bad_badge["color"] in {"yellow", "orange"}
    assert "unrated" not in good_badge["message"]


def test_badge_says_unrated_when_the_card_is_unrated(tmp_path):
    """A badge must not publish a bare number the rest of the tool withholds.

    The badge is the most-copied artifact adduce emits and the only one that
    reaches a reader with no surrounding context, so it carries the same
    qualification the terminal prints beside the score.
    """
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    assert not result.card.rated
    assert result.card.total is not None
    badge = json.loads(RENDERERS["badge"](result))
    assert "unrated" in badge["message"]
    assert badge["color"] == "lightgrey"
    assert "<svg" in RENDERERS_SVG(result)


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


def _terminal_text(result, findings, analysable_lines=None, verbose=False):
    """Render the terminal report over a constructed score card."""
    from rich.console import Console

    from adduce.profiles import load_profile
    from adduce.report import terminal
    from adduce.scoring import score

    result.card = score(findings, load_profile("default"), analysable_lines=analysable_lines)
    console = Console(width=200, record=True, force_terminal=False, legacy_windows=False)
    terminal.render(result, console, verbose=verbose)
    return console.export_text()


def _terminal_prose(result, findings, analysable_lines=None):
    """The rendered report with wrapping collapsed, so sentences can be matched."""
    return " ".join(_terminal_text(result, findings, analysable_lines).split())


def _category_finding(rule_id, category, status, weight=3, items=()):
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
        items=tuple(items),
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


#: The clause that explains a missing tier by thin source. Correct for a card
#: that has a score; a lie about a card on which nothing was assessed.
_THIN_SOURCE_CAUSE = "answered by absence rather than evidence"


def test_the_no_tier_note_names_the_missing_assessment_and_not_thin_source(tmp_path):
    """The note has to name the cause the tier names, including when both hold."""
    from adduce.rules.base import Category, Status
    from adduce.scoring import UNASSESSED_TIER

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    nothing_applied = [_category_finding("A", Category.DATA, Status.NOT_APPLICABLE)]

    thin = _terminal_prose(result, nothing_applied, analysable_lines=10)
    ample = _terminal_prose(result, nothing_applied, analysable_lines=1000)

    for prose in (thin, ample):
        assert UNASSESSED_TIER in prose
        assert "No tier assigned: no check reached an assessment" in prose
        assert _THIN_SOURCE_CAUSE not in prose
        assert "0 of 0" not in prose
    # On a card that is also unrated the parsed source is reported as a second
    # fact, and only there.
    assert "parsed 10 lines of source" in thin
    assert "lines of source" not in ample


def test_the_no_tier_note_still_names_thin_source_for_a_card_with_a_score(tmp_path):
    """The regression guard: the scored branch of the note is unchanged."""
    from adduce.rules.base import Category, Status
    from adduce.scoring import UNRATED_TIER

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    prose = _terminal_prose(
        result,
        [
            _category_finding("A", Category.CODE_EXECUTION, Status.PASS),
            _category_finding("B", Category.DATA, Status.FAIL),
        ],
        analysable_lines=10,
    )

    assert UNRATED_TIER in prose
    assert result.card.total is not None
    assert (
        "No tier assigned: only 10 lines of source were parsed, and 2 of 2 "
        "applicable checks reached a verdict."
    ) in prose
    assert _THIN_SOURCE_CAUSE in prose


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


#: Item count for the completeness assertions: large enough that a silent cap
#: or a truncating join would be visible, small enough to stay a fast test.
_ITEM_COUNT = 750
_CENSUS = "750 item(s) not listed here: 562 pass, 188 fail"


def _items(count=_ITEM_COUNT):
    from adduce.rules.base import FindingItem, Location, Status

    return tuple(
        FindingItem(
            id=f"assertion:{index}",
            status=Status.FAIL if index % 4 == 0 else Status.PASS,
            message=f"observation {index}",
            confidence=0.5,
            locations=(Location("paper.tex", index + 1),),
            remediation="restate the figure",
            kind="assertion",
            attributes={"index": index, "quoted": False},
        )
        for index in range(count)
    )


def _carrying_items(tmp_path, items, status=None):
    """A real `CheckResult` whose card holds one item-bearing finding."""
    from adduce.profiles import load_profile
    from adduce.rules.base import Category, Status
    from adduce.scoring import score

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    result.card = score(
        [_category_finding("R-ITEMS-001", Category.DRIFT, status or Status.FAIL, items=items)],
        load_profile("default"),
    )
    return result


def test_json_report_stamps_the_document_schema_beside_the_tool_version(tmp_path):
    _write(tmp_path, WELL_FORMED)
    payload = json.loads(RENDERERS["json"](run_check(tmp_path)))

    assert payload["schema"] == {"name": "adduce-report", "version": 1}
    assert payload["tool"]["name"] == "adduce"


def test_json_serialises_every_finding_item(tmp_path):
    """Machine-readable output carries all children, uncapped and in order."""
    items = _items()
    result = _carrying_items(tmp_path, items)

    reported = json.loads(RENDERERS["json"](result))["findings"][0]["items"]

    assert [entry["id"] for entry in reported] == [item.id for item in items]
    assert reported[0]["attributes"] == {"index": 0, "quoted": False}
    assert reported[4]["locations"] == [{"path": "paper.tex", "line": 5}]
    assert reported[1]["status"] == "pass"
    assert reported[0]["kind"] == "assertion"


def test_sarif_carries_every_finding_item_and_stays_schema_valid(tmp_path):
    from jsonschema import Draft7Validator

    from tests.test_schema_conformance import _FORMAT_CHECKER, _load_schema

    items = _items()
    result = _carrying_items(tmp_path, items)

    report = json.loads(RENDERERS["sarif"](result))
    reported = report["runs"][0]["results"][0]["properties"]["adduceFindingItems"]

    assert [entry["id"] for entry in reported] == [item.id for item in items]
    assert reported[0]["attributes"] == {"index": 0, "quoted": False}
    Draft7Validator(
        _load_schema("sarif-schema-2.1.0.json"), format_checker=_FORMAT_CHECKER
    ).validate(report)


def test_sarif_leaves_a_childless_finding_exactly_as_it_was(tmp_path):
    result = _carrying_items(tmp_path, ())

    sarif_result = json.loads(RENDERERS["sarif"](result))["runs"][0]["results"][0]

    assert "properties" not in sarif_result


def test_sarif_drops_a_non_actionable_findings_items_entirely_while_json_keeps_them(tmp_path):
    """Documented intent, not an oversight: SARIF encodes PASS by absence.

    A PASS finding never reaches ``_LEVELS``, so its whole result -- including
    any items it carries -- never reaches SARIF, no matter how many there are.
    JSON has no such filter: it is the format that carries every finding.
    """
    from adduce.rules.base import Status

    items = _items()
    result = _carrying_items(tmp_path, items, status=Status.PASS)

    sarif = json.loads(RENDERERS["sarif"](result))
    assert sarif["runs"][0]["results"] == []
    sarif_text = json.dumps(sarif)
    assert not any(item.id in sarif_text for item in items)

    reported = json.loads(RENDERERS["json"](result))["findings"][0]["items"]
    assert [entry["id"] for entry in reported] == [item.id for item in items]


def test_markdown_and_terminal_state_the_complete_item_count(tmp_path):
    """Human output summarises, and says how many children the summary covers."""
    from adduce.rules.base import Category, Status

    items = _items()
    result = _carrying_items(tmp_path, items)
    findings = [_category_finding("R-ITEMS-001", Category.DRIFT, Status.FAIL, items=items)]

    markdown_text = RENDERERS["markdown"](result)
    terminal_text = " ".join(_terminal_text(result, findings, verbose=True).split())

    for rendered in (markdown_text, terminal_text):
        assert _CENSUS in rendered
        # A summary that showed some children could be read as all of them.
        assert "observation 0" not in rendered
        assert "assertion:0" not in rendered


def test_a_finding_without_items_renders_no_census_at_all(tmp_path):
    from adduce.rules.base import Category, Status

    result = _carrying_items(tmp_path, ())
    findings = [_category_finding("R-ITEMS-001", Category.DRIFT, Status.FAIL)]

    markdown_text = RENDERERS["markdown"](result)
    terminal_text = _terminal_text(result, findings, verbose=True)

    for rendered in (markdown_text, terminal_text):
        assert "item(s) not listed here" not in rendered
        assert "0 item" not in rendered


def test_the_item_census_is_absent_at_default_verbosity(tmp_path):
    """The findings table -- and its census -- is verbose-only; default output has neither."""
    from adduce.rules.base import Category, Status

    items = _items()
    result = _carrying_items(tmp_path, items)
    findings = [_category_finding("R-ITEMS-001", Category.DRIFT, Status.FAIL, items=items)]

    terminal_text = _terminal_text(result, findings, verbose=False)

    assert _CENSUS not in terminal_text
    assert "item(s) not listed here" not in terminal_text


def test_default_output_says_child_results_exist_without_listing_them(tmp_path):
    """Default output lists no child, so it has to say that children are there.

    The count is the whole disclosure: a reader who sees a verdict backed by
    hundreds of observations can tell they exist and where to read them,
    without the summary turning into the list it is a summary of.
    """
    from adduce.rules.base import Category, Status

    items = _items()
    result = _carrying_items(tmp_path, items)
    findings = [_category_finding("R-ITEMS-001", Category.DRIFT, Status.FAIL, items=items)]

    terminal_text = _terminal_text(result, findings, verbose=False)

    assert f"{len(items)} structured observation(s) attached to 1 finding(s)" in terminal_text
    assert "--format json" in terminal_text
    for item in items:
        assert item.id not in terminal_text
        assert item.message not in terminal_text


def test_a_report_with_no_child_results_says_nothing_about_them(tmp_path):
    """The line is absent when there is nothing to disclose, so every ordinary
    run -- no built-in rule attaches children -- is unchanged."""
    from adduce.rules.base import Category, Status

    result = _carrying_items(tmp_path, ())
    findings = [_category_finding("R-ITEMS-001", Category.DRIFT, Status.FAIL)]

    terminal_text = _terminal_text(result, findings, verbose=False)

    assert "structured observation" not in terminal_text


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite constant in document: {value}")


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_json_report_refuses_to_serialise_a_non_finite_confidence(tmp_path, confidence):
    """`allow_nan=False` raises here, instead of shipping a document a strict parser rejects."""
    from adduce.rules.base import Category, Status

    result = _carrying_items(tmp_path, ())
    finding = result.card.findings[0]
    assert finding.category is Category.DRIFT and finding.status is Status.FAIL
    finding.confidence = confidence

    with pytest.raises(ValueError):
        RENDERERS["json"](result)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_sarif_report_refuses_to_serialise_a_non_finite_item_confidence(tmp_path, confidence):
    """Every finding item SARIF carries is JSON too; the same defence applies."""
    item = _items(1)[0]
    object.__setattr__(item, "confidence", confidence)
    result = _carrying_items(tmp_path, (item,))

    with pytest.raises(ValueError):
        RENDERERS["sarif"](result)


def test_json_report_stays_strictly_parseable_when_confidence_is_finite(tmp_path):
    """The defence does not fire on the values it was never meant to catch."""
    result = _carrying_items(tmp_path, _items(3))

    rendered = RENDERERS["json"](result)

    json.loads(rendered, parse_constant=_reject_non_finite)


def test_latex_states_the_item_census_for_a_finding_that_carries_items(tmp_path):
    """The comment listing a gap states the child count, like markdown and terminal."""
    items = _items()
    result = _carrying_items(tmp_path, items)

    output = RENDERERS["latex"](result)

    assert _CENSUS in output
    assert "observation 0" not in output
    assert "assertion:0" not in output


def test_latex_states_the_item_census_in_the_itemize_block_too(tmp_path):
    """A passed finding carrying items is also listed, not just gaps."""
    from adduce.rules.base import Status

    items = _items()
    result = _carrying_items(tmp_path, items, status=Status.PASS)

    output = RENDERERS["latex"](result)
    itemize_lines = [line for line in output.splitlines() if line.startswith(r"  \item")]

    assert any(_CENSUS in line for line in itemize_lines)


def test_latex_renders_a_finding_without_items_byte_identically(tmp_path):
    """No built-in rule emits items, so this is the shape of every real report."""
    result = _carrying_items(tmp_path, ())

    output = RENDERERS["latex"](result)

    assert output.splitlines()[-1] == "% - R-ITEMS-001: R-ITEMS-001 message"
    assert "item(s) not listed here" not in output


# -- SARIF location anchors ---------------------------------------------------


def _anchor_uris(result):
    sarif = json.loads(RENDERERS["sarif"](result))
    return [
        location["physicalLocation"]["artifactLocation"]["uri"]
        for item in sarif["runs"][0]["results"]
        for location in item["locations"]
    ]


def _inventory(result):
    return {str(entry.path) for entry in result.repo.files}


def test_sarif_anchors_a_repository_level_finding_to_an_inventoried_file(tmp_path):
    """A report must not point at a file the repository does not contain."""
    _write(tmp_path, {"train.py": "import torch\nnet = torch.nn.Linear(2, 2)\n"})
    result = run_check(tmp_path)

    uris = _anchor_uris(result)

    assert uris
    assert set(uris) <= _inventory(result)
    assert "README.md" not in uris


def test_sarif_repository_level_anchor_does_not_depend_on_inventory_order(tmp_path):
    _write(tmp_path, {"b/nested.py": "x = 1\n", "train.py": "y = 2\n", "setup.cfg": "[x]\n"})
    result = run_check(tmp_path)

    forward = _anchor_uris(result)
    result.repo.files.reverse()

    assert _anchor_uris(result) == forward
    assert set(forward) == {"setup.cfg"}


def test_sarif_prefers_the_repo_root_readme_when_one_was_inventoried(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)

    locationless = [
        finding.rule_id
        for finding in result.card.findings
        if not finding.locations and finding.status.value in {"fail", "partial"}
    ]

    assert locationless
    assert "README.md" in _anchor_uris(result)


def test_sarif_accepts_a_readme_in_another_spelling_as_the_anchor(tmp_path):
    _write(tmp_path, {"README.rst": "Demo\n", "train.py": "y = 2\n"})
    result = run_check(tmp_path)

    assert set(_anchor_uris(result)) == {"README.rst"}


@pytest.mark.parametrize("files", [BARE, WELL_FORMED], ids=["bare", "well_formed"])
def test_sarif_anchors_every_result_to_an_inventoried_path(tmp_path, files):
    _write(tmp_path, files)
    result = run_check(tmp_path)

    uris = _anchor_uris(result)

    assert uris
    assert set(uris) <= _inventory(result)


def _located(rule_id, locations):
    from adduce.rules.base import Category, Finding, Status

    return Finding(
        rule_id=rule_id,
        category=Category.DRIFT,
        title=rule_id,
        status=Status.FAIL,
        confidence=0.8,
        message="message",
        remediation="",
        weight=3,
        locations=tuple(locations),
    )


def _rendered_locations(tmp_path, findings):
    from adduce.profiles import load_profile
    from adduce.scoring import score

    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    result.card = score(findings, load_profile("default"))
    reported = json.loads(RENDERERS["sarif"](result))["runs"][0]["results"]
    return {item["ruleId"]: item["locations"][0]["physicalLocation"] for item in reported}


def test_sarif_leaves_a_file_scoped_location_file_level(tmp_path):
    """A region says where a result was detected, and no line was observed here."""
    from adduce.rules.base import Location

    file_scoped = _located("R-ANCHOR-001", [Location("configs/main.yaml")])
    with_line = _located("R-ANCHOR-002", [Location("train.py", 7)])
    anchored = _rendered_locations(tmp_path, [file_scoped, with_line])

    assert "region" not in anchored["R-ANCHOR-001"]
    assert anchored["R-ANCHOR-001"]["artifactLocation"]["uri"] == "configs/main.yaml"
    assert anchored["R-ANCHOR-002"]["region"] == {"startLine": 7}


def test_sarif_gives_the_repository_level_anchor_a_start_line(tmp_path):
    """The anchor is wholly synthetic, so line 1 navigates and claims nothing."""
    from adduce.rules.base import Category, Status

    repo_level = _category_finding("R-ANCHOR-003", Category.DRIFT, Status.FAIL)
    anchored = _rendered_locations(tmp_path, [repo_level])

    assert anchored["R-ANCHOR-003"]["region"] == {"startLine": 1}
    assert anchored["R-ANCHOR-003"]["artifactLocation"]["uri"] == "README.md"


@pytest.mark.parametrize("line", [0, -1])
def test_sarif_refuses_to_serialise_a_line_that_is_not_1_based(tmp_path, line):
    """Collectors number lines from 1, so a non-positive one is a bug, not a region."""
    from adduce.rules.base import Location

    with pytest.raises(ValueError, match="not 1-based"):
        _rendered_locations(tmp_path, [_located("R-ANCHOR-004", [Location("train.py", line)])])


def test_sarif_reports_no_location_when_nothing_was_inventoried(tmp_path):
    """An empty repository offers no file to anchor to, so no anchor is stated."""
    from jsonschema import Draft7Validator

    from tests.test_schema_conformance import _FORMAT_CHECKER, _load_schema

    result = run_check(tmp_path)
    report = json.loads(RENDERERS["sarif"](result))
    reported = report["runs"][0]["results"]

    assert result.repo.files == []
    assert reported
    assert all(item["locations"] == [] for item in reported)
    assert all(item["partialFingerprints"]["adduceFindingKey"] for item in reported)
    Draft7Validator(
        _load_schema("sarif-schema-2.1.0.json"), format_checker=_FORMAT_CHECKER
    ).validate(report)
