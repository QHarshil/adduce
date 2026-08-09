"""Two rules stopped globbing the inventory from inside ``evaluate``.

Both now read typed evidence a collector produced during a pass it was already
making. Neither is allowed to change a finding, so what these tests pin is the
equivalence itself rather than the new shape.

The ablation case had no test at all before this, and none of the thirty corpus
and synthetic targets contains a path naming an ablation — so the byte-identity
check across those targets says nothing about it. That is the whole reason this
file exists.

The matching detail that matters: ``fnmatch`` normalises case on Windows and
not on POSIX. Replacing it with a lower-cased substring test would have moved
findings on one platform only, which is the class of defect that has escaped
this project twice.
"""

from __future__ import annotations

import fnmatch

from adduce.evidence.data import DATA_DIRECTORY_NAMES, collect_data
from adduce.evidence.run_history import ABLATION_PATTERN, collect_run_history
from adduce.rules.base import Status

ABLATION_FILES = {
    "ablations/run.sh": "python train.py --ablate heads\n",
    "configs/ablation_heads.yaml": "heads: 4\n",
    "results/ablate_layers.csv": "layers,acc\n1,0.5\n",
    "src/train.py": "import torch\n",
    "docs/notes.md": "no marker here\n",
}


def test_ablation_paths_match_the_glob_they_replaced(make_repo) -> None:
    """The collector must return exactly what ``repo.find`` did, in order."""
    repo = make_repo(ABLATION_FILES)
    expected = [str(entry.path) for entry in repo.find(ABLATION_PATTERN)]
    assert collect_run_history(repo).ablation_paths == expected
    assert expected, "fixture must actually contain ablation paths"


def test_ablation_matching_follows_fnmatch_not_a_substring(make_repo) -> None:
    """Pins the platform-sensitive half: what matches here is what fnmatch says."""
    repo = make_repo({**ABLATION_FILES, "ABLATION_UPPER.md": "x\n"})
    collected = collect_run_history(repo).ablation_paths
    for entry in repo.files:
        relative = str(entry.path)
        assert (relative in collected) == fnmatch.fnmatch(relative, ABLATION_PATTERN)


def test_a_repository_with_no_ablation_artifacts_reports_none(make_repo) -> None:
    assert collect_run_history(make_repo({"src/train.py": "x = 1\n"})).ablation_paths == []


def test_data_layout_matches_the_scan_it_replaced(make_repo) -> None:
    repo = make_repo(
        {
            "data/raw/a.csv": "x\n",
            "data/processed/b.csv": "x\n",
            "datasets/Splits/c.csv": "x\n",
            # Directly under data/, so the depth guard is exercised: this file
            # names no subdirectory and must contribute none.
            "data/loose.csv": "x\n",
            "src/train.py": "x = 1\n",
            "README.md": "x\n",
        }
    )
    evidence = collect_data(repo)
    expected_top = {
        entry.path.parts[0].lower() for entry in repo.files if len(entry.path.parts) > 1
    }
    expected_sub = {
        entry.path.parts[1].lower()
        for entry in repo.files
        if len(entry.path.parts) > 2 and entry.path.parts[0].lower() in DATA_DIRECTORY_NAMES
    }
    assert evidence.top_level_directories == expected_top
    assert evidence.data_subdirectories == expected_sub
    assert evidence.data_subdirectories == {"raw", "processed", "splits"}


def test_a_file_at_the_root_contributes_no_directory(make_repo) -> None:
    evidence = collect_data(make_repo({"README.md": "x\n"}))
    assert evidence.top_level_directories == set()
    assert evidence.data_subdirectories == set()


# -- the rules themselves --------------------------------------------------

PAPER = (
    "\\documentclass{article}\n\\begin{document}\n"
    "We include an ablation over attention heads.\n"
    "\\end{document}\n"
)


def _finding(evidence, rule_id: str):
    from adduce.rules import discover_rules

    (rule,) = [candidate for candidate in discover_rules() if candidate.id == rule_id]
    return rule.evaluate(evidence)


def test_the_ablation_rule_passes_when_an_artifact_exists(make_evidence) -> None:
    evidence = make_evidence({**ABLATION_FILES, "paper/main.tex": PAPER})
    from adduce.evidence.latex import collect_latex

    evidence.latex = collect_latex(evidence.repo)
    assert evidence.latex.ablation_mentions
    assert _finding(evidence, "R-DRIFT-006").status is Status.PASS


def test_the_ablation_rule_is_partial_when_the_paper_mentions_what_the_repo_lacks(
    make_evidence,
) -> None:
    evidence = make_evidence({"src/train.py": "x = 1\n", "paper/main.tex": PAPER})
    from adduce.evidence.latex import collect_latex

    evidence.latex = collect_latex(evidence.repo)
    assert _finding(evidence, "R-DRIFT-006").status is Status.PARTIAL


def test_the_data_layout_rule_reads_the_collected_layout(make_evidence) -> None:
    evidence = make_evidence({"data/raw/a.csv": "x\n", "data/processed/b.csv": "y\n"})
    assert _finding(evidence, "R-DATA-006").status is Status.PASS


def test_the_data_layout_rule_is_not_applicable_without_a_data_directory(make_evidence) -> None:
    evidence = make_evidence({"src/train.py": "x = 1\n"})
    assert _finding(evidence, "R-DATA-006").status is Status.NOT_APPLICABLE
