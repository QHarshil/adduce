"""End-to-end pipeline: scan → collect → evaluate → suppress → score."""

from __future__ import annotations

import json
import subprocess

from adduce.engine import baseline_snapshot, regressions_against, run_check
from adduce.rules.base import Status

WELL_FORMED = {
    "README.md": (
        "# Demo\n\n## Installation\n\n```bash\npip install -r requirements.txt\n```\n\n"
        "## Reproducing results\n\n```bash\nbash run.sh\npython train.py --config configs/main.yaml\n```\n\n"
        "## Expected results\n\n| Metric | Value |\n|---|---|\n| Acc | 92.1 |\n\n"
        "## Hardware\n\n1x NVIDIA A100, ~2 hours. Results from commit abc1234.\n\n"
        "## Data\n\nhttps://zenodo.org/record/1234567 (DOI: 10.5281/zenodo.1234567), see scripts/download_data.sh\n"
    ),
    "LICENSE": "MIT License\n",
    "CITATION.cff": "cff-version: 1.2.0\ntitle: demo\n",
    "requirements.txt": "torch==2.1.0\nnumpy==1.26.0\npyyaml==6.0.1\n",
    ".python-version": "3.11\n",
    "Dockerfile": "FROM python:3.11-slim\n",
    "run.sh": "#!/bin/bash\npython train.py\n",
    "SHA256SUMS": "abc data.tar\n",
    "scripts/download_data.sh": "curl -O https://example.org/d.tar\n",
    "configs/main.yaml": "lr: 0.001\n",
    "train.py": (
        "import argparse\nimport random\nimport yaml\nimport numpy as np\nimport torch\n"
        "from torch.utils.data import DataLoader\n\n"
        "def set_seed(seed):\n"
        "    random.seed(seed)\n"
        "    np.random.seed(seed)\n"
        "    torch.manual_seed(seed)\n"
        "    torch.cuda.manual_seed_all(seed)\n"
        "    torch.backends.cudnn.deterministic = True\n"
        "    torch.backends.cudnn.benchmark = False\n\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--config')\n"
        "    args = parser.parse_args()\n"
        "    with open(args.config) as f:\n"
        "        cfg = yaml.safe_load(f)\n"
        "    set_seed(0)\n"
        "    g = torch.Generator()\n"
        "    g.manual_seed(0)\n"
        "    loader = DataLoader(None, shuffle=True, generator=g, num_workers=2, worker_init_fn=id)\n\n"
        "if __name__ == '__main__':\n    main()\n"
    ),
}

BARE = {
    "model.py": "import torch\nnet = torch.nn.Linear(2, 2)\n",
    "requirements.txt": "torch\n",
}


def _write(root, files):
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_well_formed_repo_scores_high(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    assert result.card.total >= 75, result.card.to_dict()
    seed = next(f for f in result.card.findings if f.rule_id == "R-DET-001")
    assert seed.status is Status.PASS


def test_bare_repo_scores_low(tmp_path):
    _write(tmp_path, BARE)
    result = run_check(tmp_path)
    assert result.card.total <= 40, result.card.to_dict()


def test_scores_separate_good_from_bad(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"
    good_root.mkdir()
    bad_root.mkdir()
    _write(good_root, WELL_FORMED)
    _write(bad_root, BARE)
    assert run_check(good_root).card.total - run_check(bad_root).card.total >= 40


def test_inline_suppression_marks_finding(tmp_path):
    files = dict(BARE)
    files["model.py"] = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(None, shuffle=True)  # adduce: ignore=R-DET-004\n"
    )
    _write(tmp_path, files)
    result = run_check(tmp_path)
    finding = next(f for f in result.card.findings if f.rule_id == "R-DET-004")
    assert finding.suppressed


def test_config_ignore_suppresses(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = 'ignore = ["R-LIC-001"]\n'
    _write(tmp_path, files)
    result = run_check(tmp_path)
    finding = next(f for f in result.card.findings if f.rule_id == "R-LIC-001")
    assert finding.suppressed


def test_config_profile_and_cli_override(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = 'profile = "acm"\n'
    _write(tmp_path, files)
    assert run_check(tmp_path).card.profile_name == "acm"
    assert run_check(tmp_path, profile_name="strict").card.profile_name == "strict"


def test_exclude_directories(tmp_path):
    files = dict(WELL_FORMED)
    files["third_party/vendor.py"] = "from sklearn.cluster import KMeans\nKMeans()\n"
    _write(tmp_path, files)
    with_vendor = run_check(tmp_path)
    without_vendor = run_check(tmp_path, exclude=("third_party",))
    ids_with = {f.rule_id: f.status for f in with_vendor.card.findings}
    ids_without = {f.rule_id: f.status for f in without_vendor.card.findings}
    assert ids_with.get("R-DET-006") in (Status.FAIL, Status.PARTIAL)
    assert ids_without.get("R-DET-006", Status.NOT_APPLICABLE) is Status.NOT_APPLICABLE


def test_baseline_regression_detection(tmp_path):
    _write(tmp_path, WELL_FORMED)
    good = run_check(tmp_path)
    snapshot = baseline_snapshot(good.card)
    assert regressions_against(good.card, snapshot) == []

    # Degrade determinism: strip the seeding helper.
    (tmp_path / "train.py").write_text(
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(None, shuffle=True)\n",
        encoding="utf-8",
    )
    worse = run_check(tmp_path)
    regressed_ids = {f.rule_id for f in regressions_against(worse.card, snapshot)}
    assert "R-DET-001" in regressed_ids


def test_new_rules_are_not_regressions(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    empty_baseline = {"version": 1, "rules": {}}
    assert regressions_against(result.card, empty_baseline) == []


def test_git_metadata_collected(tmp_path):
    _write(tmp_path, WELL_FORMED)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "tag", "v1.0"], cwd=tmp_path, check=True)
    result = run_check(tmp_path)
    vcs = next(f for f in result.card.findings if f.rule_id == "R-VER-002")
    assert vcs.status is Status.PASS
    assert result.repo.git.head_commit


def test_json_serialisation_round_trip(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    payload = json.loads(json.dumps(result.card.to_dict()))
    assert payload["total"] == round(result.card.total, 1)
    assert {c["category"] for c in payload["categories"]}
