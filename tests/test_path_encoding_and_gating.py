"""Three fixes, held in place: Git path encoding and the byte-order mark, line
numbering under separators the tokenizer does not recognise, and gating, the
baseline path, and repository-derived text in prose output."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest
from rich.console import Console
from typer.testing import CliRunner

from adduce.cli import BASELINE_FILENAME, app
from adduce.engine import run_check
from adduce.evidence import collect
from adduce.model import scan_repository
from adduce.naming import canonical_hyperparameter
from adduce.report import terminal as terminal_report
from adduce.report.json_report import render as render_json
from adduce.report.markdown import render as render_markdown
from tests.conftest import plain
from tests.test_cli_new import _git
from tests.test_engine import BARE, _write

runner = CliRunner(env={"COLUMNS": "300"})

#: A filename carrying a screen-clearing sequence and an OSC 8 hyperlink. The
#: hyperlink target uses ``mailto:`` because a URL's slashes would become path
#: separators.
CONTROL_NAME = "ev\x1b[2Jil\x1b]8;;mailto:attacker@example.com\x1b\\click\x1b]8;;\x1b\\.py"

#: Every separator ``str.splitlines`` breaks on that a line of source is not
#: broken by. Only the form feed is legal outside a string in Python source.
EXOTIC_SEPARATORS = "\x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029"


def _real_line_count(text):
    return text.count("\n") + (0 if text.endswith("\n") else 1)


# --------------------------------------------------------------------------
# Fix 1 - Git path encoding, and a byte-order mark
# --------------------------------------------------------------------------


def test_scan_reports_a_non_ascii_tracked_path_unquoted(tmp_path):
    """core.quotePath=false: the tracked set holds real paths, not C-escapes."""
    (tmp_path / "café.py").write_text("import torch\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")

    repo = scan_repository(tmp_path)

    tracked = repo.git.tracked_files
    assert tracked == {str(entry.path) for entry in repo.files}
    assert any(not entry.isascii() for entry in tracked)
    # "caf\303\251.py", quotes included, is what quoting produced.
    assert all('"' not in entry and "\\" not in entry for entry in tracked)


def test_committed_non_ascii_weights_get_the_status_ascii_weights_get(tmp_path):
    def committed_weights(name):
        root = tmp_path / name.replace(".", "-")
        root.mkdir()
        (root / "train.py").write_text("import torch\n", encoding="utf-8")
        (root / "requirements.txt").write_text("torch\n", encoding="utf-8")
        (root / name).write_bytes(b"\x00" * 4096)
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
        return next(f for f in run_check(root).card.findings if f.rule_id == "R-DATA-004")

    ascii_finding = committed_weights("model.pt")
    non_ascii_finding = committed_weights("modèle.pt")

    assert ascii_finding.status.value == "fail"
    assert non_ascii_finding.status is ascii_finding.status
    assert non_ascii_finding.message == ascii_finding.message
    assert any("modèle.pt" in location.path for location in non_ascii_finding.locations)


def test_diff_classifies_a_commit_touching_only_a_non_ascii_path(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "café.py").write_text("import torch\nx = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "café.py").write_text("import torch\nx = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "change code only")

    result = runner.invoke(app, ["diff", "HEAD~1..HEAD", str(tmp_path)])

    assert result.exit_code == 1, result.output
    output = plain(result.output)
    assert "code (1)" in output
    assert "may now be stale" in output
    assert "nothing substantive changed" not in output


def test_scan_reports_a_tracked_filename_containing_a_newline(tmp_path):
    """A newline is legal in a filename; -z and NUL splitting are what survive it."""
    try:
        (tmp_path / "we\nird.py").write_text("import torch\n", encoding="utf-8")
    except (OSError, ValueError):
        pytest.skip("this platform refuses a newline in a filename")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")

    repo = scan_repository(tmp_path)

    assert repo.git.tracked_files == {"we\nird.py"}


def test_byte_order_mark_keeps_a_module_in_the_evidence(tmp_path):
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "seeding.py").write_text("import random\nimport torch\n", encoding="utf-8-sig")
    assert (tmp_path / "seeding.py").read_bytes().startswith(b"\xef\xbb\xbf")

    evidence = collect(scan_repository(tmp_path))

    module = next(m for m in evidence.py.modules if m.path == "seeding.py")
    assert module.parse_error is False
    assert {"random", "torch"} <= evidence.py.imports


def test_byte_order_mark_does_not_flip_the_seeding_status(tmp_path):
    """The only seeding lives in a BOM'd module; R-DET-001 must not deny it."""
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "train.py").write_text(
        "from seeding import set_all_seeds\n\nset_all_seeds(0)\n", encoding="utf-8"
    )
    (tmp_path / "seeding.py").write_text(
        "import random\n"
        "import torch\n"
        "\n\n"
        "def set_all_seeds(seed):\n"
        "    random.seed(seed)\n"
        "    torch.manual_seed(seed)\n"
        "    torch.cuda.manual_seed_all(seed)\n",
        encoding="utf-8-sig",
    )

    finding = next(f for f in run_check(tmp_path).card.findings if f.rule_id == "R-DET-001")

    assert finding.status.value == "pass"
    assert "No seeding detected" not in finding.message


def _repository_with_non_ascii_refs(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.py").write_text("import torch\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "caractéristique")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "tag", "versión-1.0")
    return root


def test_scan_reports_a_non_ascii_tag(tmp_path):
    repo = scan_repository(_repository_with_non_ascii_refs(tmp_path))

    assert repo.git.is_repo
    assert repo.git.head_commit is not None
    assert repo.git.tags == ("versión-1.0",)


#: Reports the scan's answer in pure ASCII, because the locale the child runs
#: under cannot encode the tag it is reporting.
_SCAN_PROBE = """
import codecs
import json
import locale
import sys
from pathlib import Path

from adduce.model import scan_repository

repo = scan_repository(Path(sys.argv[1]))
print(json.dumps({
    "codec": codecs.lookup(locale.getencoding()).name,
    "is_repo": repo.git.is_repo,
    "head": repo.git.head_commit is not None,
    "tags": list(repo.git.tags),
}))
"""


def test_scan_survives_git_metadata_the_locale_codec_cannot_decode(tmp_path):
    """A separate process, with the interpreter's UTF-8 fallbacks switched off.

    Not the Windows cp1252 path — that one rests on the explicit ``encoding=``
    and the widened handler, not on any run here — but the same failure class:
    ``text=True`` decodes Git's output with the locale codec, and a tag name is
    repository-controlled.
    """
    root = _repository_with_non_ascii_refs(tmp_path / "repo")
    probe = tmp_path / "probe.py"
    probe.write_text(_SCAN_PROBE, encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(probe), str(root)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
            "LC_ALL": "C",
            "LANG": "C",
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    if payload["codec"].startswith("utf"):
        pytest.skip(f"this platform coerces the C locale to {payload['codec']}")
    assert payload["is_repo"] and payload["head"]
    assert payload["tags"] == ["versión-1.0"]


# --------------------------------------------------------------------------
# Fix 2 - line numbering
# --------------------------------------------------------------------------

def test_form_feed_keeps_a_pragma_on_the_line_it_was_written_for(tmp_path):
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "model.py").write_text(
        "import torch\n"
        "from torch.utils.data import DataLoader\n"
        "\f\n"
        "loader = DataLoader(None, shuffle=True)  # adduce: ignore=R-DET-004\n",
        encoding="utf-8",
    )

    finding = next(f for f in run_check(tmp_path).card.findings if f.rule_id == "R-DET-004")

    assert [(loc.path, loc.line) for loc in finding.locations] == [("model.py", 4)]
    assert finding.suppressed


def test_form_feed_does_not_move_a_pragma_onto_another_finding(tmp_path):
    """A pragma written for line 4 must not suppress the finding on line 5."""
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "model.py").write_text(
        "import torch\n"
        "from torch.utils.data import DataLoader\n"
        "\f\n"
        "# adduce: ignore=R-DET-004\n"
        "loader = DataLoader(None, shuffle=True)\n",
        encoding="utf-8",
    )

    result = run_check(tmp_path)

    assert result.evidence.py.suppressions == {"model.py": {4: {"R-DET-004"}}}
    finding = next(f for f in result.card.findings if f.rule_id == "R-DET-004")
    assert [(loc.path, loc.line) for loc in finding.locations] == [("model.py", 5)]
    assert not finding.suppressed


def test_no_reported_line_points_past_the_end_of_its_file(tmp_path):
    source = (
        "import torch\n"
        "from torch.utils.data import DataLoader\n"
        "\f\n"
        "DATA = '/Users/someone/data/train.csv'\n"
        "\f\n"
        "ENDPOINT = 'localhost:8000'\n"
        "loader = DataLoader(None, shuffle=True)\n"
    )
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "paths.py").write_text(source, encoding="utf-8")
    limit = _real_line_count(source)

    result = run_check(tmp_path)

    reported = [
        (finding.rule_id, location.line)
        for finding in result.card.findings
        for location in finding.locations
        if location.path == "paths.py" and location.line is not None
    ]
    assert len(reported) >= 3, reported
    assert [entry for entry in reported if entry[1] > limit] == []


def test_portability_hits_number_lines_by_newline_only(tmp_path):
    """Every separator splitlines() adds, on one line, above a real hit."""
    source = f"# notes\n# separators: {EXOTIC_SEPARATORS}\n/Users/someone/data\n"
    (tmp_path / "notes.md").write_text(source, encoding="utf-8")
    assert len(source.splitlines()) > _real_line_count(source)

    hits = collect(scan_repository(tmp_path)).portability.of_kind("abs_path")

    assert [(hit.file, hit.line) for hit in hits] == [("notes.md", 3)]


def test_run_commands_number_lines_by_newline_only(tmp_path):
    script = "#!/bin/bash\n# separators: \x0c\x85\n\npython train.py --lr 0.1\n"
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "run.sh").write_text(script, encoding="utf-8")
    assert len(script.splitlines()) > _real_line_count(script)

    commands = collect(scan_repository(tmp_path)).runs.commands

    assert [(c.file, c.line) for c in commands] == [("run.sh", 4)]


def test_a_result_row_is_split_only_at_a_newline(tmp_path):
    """U+2028 inside a JSON string is legal JSON; splitting there loses the row."""
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (tmp_path / "results").mkdir()
    row = '{"accuracy": 0.91, "note": "before\u2028after"}'
    assert len(row.splitlines()) == 2
    (tmp_path / "results" / "metrics.jsonl").write_text(row + "\n", encoding="utf-8")

    files = collect(scan_repository(tmp_path)).results.files

    metrics = next(f.metrics for f in files if f.path == "results/metrics.jsonl")
    assert metrics["accuracy"] == [0.91]


def test_remote_reference_lines_number_by_newline_only(tmp_path):
    """A remote reference's line is the line a reader counts to.

    The fixture is a shell script rather than prose because a bare URL in a
    Markdown file yields no reference at all — a version of this test written
    against one passes whatever the collector does.
    """
    source = (
        f"#!/bin/sh\n# separators: {EXOTIC_SEPARATORS}\n"
        "curl -L https://example.org/weights.tar -o weights.tar\n"
    )
    (tmp_path / "run.sh").write_text(source, encoding="utf-8")
    (tmp_path / "train.py").write_text("import torch\n", encoding="utf-8")

    references = collect(scan_repository(tmp_path)).remote.references

    # The premise: the reference is found at all. Without this the assertion
    # below would hold on an empty list.
    assert [ref.file for ref in references] == ["run.sh"]
    # Three lines in the file, so anything above 3 is past its end.
    assert [ref.line for ref in references] == [3]


# --------------------------------------------------------------------------
# Fix 3 - gating, the baseline path, and untrusted text in prose output
# --------------------------------------------------------------------------


def _reported_and_raw_score(root):
    rendered = runner.invoke(app, ["check", str(root), "-f", "json"])
    assert rendered.exit_code == 0, rendered.output
    return json.loads(rendered.stdout)["total"], run_check(root).card.total


def test_fail_under_accepts_the_score_every_report_shows(tmp_path):
    _write(tmp_path, BARE)
    reported, raw = _reported_and_raw_score(tmp_path)
    assert reported > raw, (
        f"premise: the reported score {reported} must round up from the raw {raw}, "
        "or the equality case does not distinguish the two comparisons"
    )

    result = runner.invoke(app, ["check", str(tmp_path), "--fail-under", str(reported)])

    assert result.exit_code == 0, plain(result.output)
    assert "--fail-under" not in plain(result.output)


def test_fail_under_below_the_threshold_states_both_sides_as_reported(tmp_path):
    _write(tmp_path, BARE)
    reported, _ = _reported_and_raw_score(tmp_path)
    threshold = round(reported + 0.1, 1)

    result = runner.invoke(app, ["check", str(tmp_path), "--fail-under", str(threshold)])

    assert result.exit_code == 1
    assert f"score {reported:g} is below --fail-under {threshold:g}" in plain(result.output)


def test_fail_on_regression_reads_the_baseline_through_a_symlinked_ancestor(tmp_path):
    real = tmp_path / "real" / "repo"
    real.mkdir(parents=True)
    _write(real, BARE)
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    viewed = tmp_path / "link" / "repo"

    recorded = runner.invoke(app, ["baseline", str(viewed)])
    assert recorded.exit_code == 0, plain(recorded.output)
    assert (real / BASELINE_FILENAME).is_file()

    gated = runner.invoke(app, ["check", str(viewed), "--fail-on-regression"])

    output = plain(gated.output)
    assert "refusing" not in output
    assert f"no {BASELINE_FILENAME} found" not in output
    assert gated.exit_code == 0, output


def _repository_with_a_control_sequence_in_a_filename(root):
    (root / "requirements.txt").write_text("torch\n", encoding="utf-8")
    (root / CONTROL_NAME).write_text(
        "import torch\n"
        "DATA = '/Users/someone/data'\n"
        "torch.backends.cuda.matmul.allow_tf32 = True\n",
        encoding="utf-8",
    )
    return run_check(root)


def _control_bearing_finding(result):
    finding = next(f for f in result.card.findings if f.rule_id == "R-PORT-001")
    assert any("\x1b" in location.path for location in finding.locations), (
        "premise: the escape sequence must reach the reporter"
    )
    return finding


def test_terminal_report_strips_control_sequences_from_paths(tmp_path):
    result = _repository_with_a_control_sequence_in_a_filename(tmp_path)
    _control_bearing_finding(result)
    buffer = io.StringIO()

    terminal_report.render(result, Console(file=buffer, width=400), verbose=True)

    rendered = buffer.getvalue()
    assert b"\x1b" not in rendered.encode("utf-8")
    assert "[2Jil" in rendered


def test_markdown_report_strips_control_sequences_from_paths(tmp_path):
    result = _repository_with_a_control_sequence_in_a_filename(tmp_path)
    _control_bearing_finding(result)

    rendered = render_markdown(result)

    assert b"\x1b" not in rendered.encode("utf-8")
    assert "[2Jil" in rendered


def test_json_report_escapes_control_sequences_instead_of_stripping(tmp_path):
    result = _repository_with_a_control_sequence_in_a_filename(tmp_path)
    _control_bearing_finding(result)

    rendered = render_json(result)

    assert b"\x1b" not in rendered.encode("utf-8")
    assert "\\u001b" in rendered
    paths = [
        location["path"]
        for finding in json.loads(rendered)["findings"]
        if finding["rule_id"] == "R-PORT-001"
        for location in finding["locations"]
    ]
    assert paths == [CONTROL_NAME]


def test_precision_event_list_strips_control_sequences(tmp_path):
    _repository_with_a_control_sequence_in_a_filename(tmp_path)

    result = runner.invoke(app, ["precision", str(tmp_path)])

    assert result.exit_code == 0
    lines = result.output.splitlines()
    start = next(i for i, line in enumerate(lines) if "Detected precision controls" in line)
    end = next(i for i in range(start + 1, len(lines)) if not lines[i].strip())
    events = "\n".join(lines[start + 1 : end])
    assert b"\x1b" not in events.encode("utf-8")
    assert "[2Jil" in events
    assert "allow_tf32" in events


def test_focused_audit_findings_table_strips_control_sequences(tmp_path):
    _repository_with_a_control_sequence_in_a_filename(tmp_path)

    result = runner.invoke(app, ["precision", str(tmp_path)])

    assert b"\x1b" not in result.output_bytes


# --------------------------------------------------------------------------
# Naming: "depth" is no longer an alias for num_layers
# --------------------------------------------------------------------------


def test_canonical_hyperparameter_separates_max_depth_from_num_layers():
    assert canonical_hyperparameter("max depth") == "max_depth"
    assert canonical_hyperparameter("max_depth") == "max_depth"
    assert canonical_hyperparameter("depth") is None
    assert canonical_hyperparameter("layers") == "num_layers"


_TREE_PAPER = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "We train a gradient-boosted tree ensemble with max depth 4 and learning rate 0.05.\n"
    "\\end{document}\n"
)


def _drift_finding(root, config):
    root.mkdir(parents=True)
    (root / "paper.tex").write_text(_TREE_PAPER, encoding="utf-8")
    (root / "train.py").write_text("import torch\n", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "main.yaml").write_text(config, encoding="utf-8")
    return next(f for f in run_check(root).card.findings if f.rule_id == "R-DRIFT-001")


def test_paper_max_depth_does_not_drift_against_num_layers(tmp_path):
    unrelated = _drift_finding(tmp_path / "layers", "num_layers: 12\nlearning_rate: 0.05\n")
    assert unrelated.status.value == "pass"
    assert "num_layers" not in unrelated.message

    agreeing = _drift_finding(tmp_path / "agree", "max_depth: 4\nlearning_rate: 0.05\n")
    assert agreeing.status.value == "pass"

    # The group is live, not merely absent: a real max_depth disagreement fires.
    mismatched = _drift_finding(tmp_path / "differ", "max_depth: 9\nlearning_rate: 0.05\n")
    assert mismatched.status.value == "fail"
    assert "max_depth: paper says 4" in mismatched.message
