"""Manifest round-trip, scaffolding, claim graph, reviewer time, and modes."""

from __future__ import annotations

from pathlib import Path

import yaml

from adduce.engine import run_check
from adduce.graph import TrailStatus
from adduce.manifest import load_manifest, write_manifest
from adduce.manifest_builder import scaffold_manifest
from adduce.modes import badge_eligibility
from tests.test_engine import BARE, WELL_FORMED, _write

_MANIFEST = {
    "schema": "adduce/1",
    "paper": {"title": "Demo Paper", "file": "paper/main.tex"},
    "environment": {"python": "3.11", "hardware": "1x A100", "cuda": "12.1"},
    "datasets": [
        {"id": "ml-25m", "source": "https://example.org/ml-25m.zip", "checksum": "sha256:abc", "license": "CC-BY"}
    ],
    "remotes": [{"call": 'AutoModel.from_pretrained("bert-base-uncased")', "revision": "8" * 40}],
    "claims": [
        {
            "id": "C1",
            "text": "Accuracy of 92.1",
            "kind": "metric",
            "where": "Table 2",
            "metric": "accuracy",
            "value": 92.1,
            "seeds": [42, 43, 44],
            "produced_by": {
                "command": "bash run.sh",
                "config": "configs/main.yaml",
                "log": "results/eval.csv",
                "commit": "abc1234",
            },
        }
    ],
    "smoke": {"command": "python train.py --smoke", "max_runtime_minutes": 5, "expected_outputs": ["out.json"]},
}


def _write_manifest_file(root: Path) -> None:
    target = root / ".adduce"
    target.mkdir(exist_ok=True)
    (target / "manifest.yaml").write_text(yaml.safe_dump(_MANIFEST), encoding="utf-8")


def test_manifest_round_trip(tmp_path):
    _write_manifest_file(tmp_path)
    manifest = load_manifest(tmp_path)
    assert manifest.exists
    assert manifest.paper.title == "Demo Paper"
    assert manifest.claims[0].seeds == [42, 43, 44]
    assert manifest.claims[0].produced_by.config == "configs/main.yaml"
    assert manifest.smoke.command == "python train.py --smoke"

    write_manifest(tmp_path, manifest)
    reloaded = load_manifest(tmp_path)
    assert reloaded.claims[0].value == 92.1
    assert (tmp_path / ".adduce" / "manifest.json").is_file()


def test_malformed_manifest_does_not_crash(tmp_path):
    (tmp_path / ".adduce").mkdir()
    (tmp_path / ".adduce" / "manifest.yaml").write_text(":\n  - not valid: [", encoding="utf-8")
    manifest = load_manifest(tmp_path)
    assert not manifest.exists


def test_scaffold_manifest_from_evidence(tmp_path):
    files = dict(WELL_FORMED)
    files["model.py"] = (
        "from transformers import AutoModel\nAutoModel.from_pretrained('bert-base-uncased')\n"
    )
    _write(tmp_path, files)
    result = run_check(tmp_path)
    draft = scaffold_manifest(result.evidence)
    assert any("bert-base-uncased" in r.call for r in draft.remotes)
    assert draft.environment.python is not None


def test_claim_graph_with_manifest(tmp_path):
    files = dict(WELL_FORMED)
    files["results/eval.csv"] = "epoch,accuracy\n1,92.07\n"
    _write(tmp_path, files)
    _write_manifest_file(tmp_path)
    result = run_check(tmp_path)
    assert result.graph.from_manifest
    trail = result.graph.trails[0]
    assert trail.status in (TrailStatus.VERIFIED, TrailStatus.PARTIAL)
    labels = {entry.label for entry in trail.entries}
    assert {"metric", "config", "log", "seeds"} <= labels
    metric_entry = next(e for e in trail.entries if e.label == "metric")
    assert metric_entry.resolved is True  # 92.07 rounds to the claimed 92.1


def test_claim_graph_flags_broken_paths(tmp_path):
    _write(tmp_path, dict(WELL_FORMED))
    manifest = dict(_MANIFEST)
    manifest["claims"] = [
        {
            "id": "C1",
            "metric": "accuracy",
            "value": 92.1,
            "produced_by": {"config": "configs/does_not_exist.yaml"},
        }
    ]
    (tmp_path / ".adduce").mkdir(exist_ok=True)
    (tmp_path / ".adduce" / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    result = run_check(tmp_path)
    trail = result.graph.trails[0]
    config_entry = next(e for e in trail.entries if e.label == "config")
    assert config_entry.resolved is False
    assert trail.status is not TrailStatus.VERIFIED


def test_reviewer_time_buckets(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"
    good_root.mkdir()
    bad_root.mkdir()
    _write(good_root, WELL_FORMED)
    _write(bad_root, BARE)
    good = run_check(good_root).reviewer_time
    bad = run_check(bad_root).reviewer_time
    assert good.bucket in {"Excellent", "Good", "Risky"}
    assert not good.unknown
    # A bare repo with no README and no runner is honestly "unknown", not a number.
    assert bad.unknown
    assert bad.factors  # names what is costing time


def test_badge_eligibility_shapes(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    assessments = badge_eligibility(result.card)
    labels = [a.label for a in assessments]
    assert "ACM Artifacts Available" in labels
    assert all("Reproduced" not in label for label in labels)  # never claimed
    functional = next(a for a in assessments if "Functional" in a.label)
    assert functional.eligible  # WELL_FORMED satisfies the functional gates
