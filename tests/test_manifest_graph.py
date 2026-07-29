"""Manifest round-trip, scaffolding, claim graph, reviewer time, and modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from adduce.engine import run_check
from adduce.graph import TrailStatus
from adduce.manifest import load_manifest, write_manifest
from adduce.manifest_builder import scaffold_manifest
from adduce.modes import badge_eligibility
from adduce.report.json_report import render as render_json
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

    roundtrip = tmp_path / "roundtrip"
    roundtrip.mkdir()
    write_manifest(roundtrip, manifest)
    reloaded = load_manifest(roundtrip)
    assert reloaded.claims[0].value == 92.1
    assert (roundtrip / ".adduce" / "manifest.json").is_file()


def test_malformed_manifest_is_recorded_as_an_error(tmp_path):
    (tmp_path / ".adduce").mkdir()
    (tmp_path / ".adduce" / "manifest.yaml").write_text(":\n  - not valid: [", encoding="utf-8")
    manifest = load_manifest(tmp_path)
    assert manifest.exists
    assert manifest.error


def test_check_rejects_malformed_manifest_structure(tmp_path):
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        "schema: adduce/1\npaper: malformed\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="paper.*mapping"):
        run_check(tmp_path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("paper:\n  title: 3\n", r"paper\.title must be a string"),
        ("environment:\n  hardware: [A100]\n", r"environment\.hardware must be a string"),
        ("datasets:\n  - id: 7\n", r"datasets\[0\]\.id is required"),
        (
            "remotes:\n  - call: model\n    revision: 7\n",
            r"remotes\[0\]\.revision must be a string",
        ),
        (
            "claims:\n  - id: C1\n    kind: metric\n    text: [claim]\n",
            r"claims\[0\]\.text must be a string",
        ),
        (
            "claims:\n  - id: C1\n    status: accepted\n",
            r"claims\[0\]\.status must be 'draft' or 'confirmed'",
        ),
        (
            "claims:\n  - id: C1\n    produced_by:\n      command: [python, train.py]\n",
            r"claims\[0\]\.produced_by\.command must be a string",
        ),
        (
            "smoke:\n  expected_outputs: [result.json, 3]\n",
            r"every smoke\.expected_outputs entry must be a string",
        ),
    ],
)
def test_manifest_rejects_scalar_coercion(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text(
        f"schema: adduce/1\n{body}",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        run_check(tmp_path)


def test_manifest_command_refuses_to_overwrite_malformed_file(tmp_path):
    from typer.testing import CliRunner

    from adduce.cli import app

    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    target = manifest_dir / "manifest.yaml"
    original = ":\n  - not valid: ["
    target.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(app, ["manifest", str(tmp_path)])

    assert result.exit_code == 2
    assert "could not parse" in result.output
    assert target.read_text(encoding="utf-8") == original


def test_unsupported_manifest_schema_is_reported(tmp_path):
    manifest_dir = tmp_path / ".adduce"
    manifest_dir.mkdir()
    (manifest_dir / "manifest.yaml").write_text("schema: adduce/99\n", encoding="utf-8")

    manifest = load_manifest(tmp_path)

    assert manifest.exists
    assert manifest.error and "unsupported manifest schema" in manifest.error


def test_manifest_refresh_writes_proposal_without_touching_author_file(tmp_path):
    from typer.testing import CliRunner

    from adduce.cli import app

    _write(tmp_path, WELL_FORMED)
    _write_manifest_file(tmp_path)
    target = tmp_path / ".adduce" / "manifest.yaml"
    original = target.read_text(encoding="utf-8") + "# author comment\n"
    target.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(app, ["manifest", str(tmp_path), "--refresh"])

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == original
    assert (tmp_path / ".adduce" / "manifest.proposed.yaml").is_file()
    assert (tmp_path / ".adduce" / "manifest.proposed.json").is_file()


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


def test_manifest_refresh_preserves_author_content_and_appends_detected_entries(tmp_path):
    files = dict(WELL_FORMED)
    files["model.py"] = (
        "from transformers import AutoModel\n"
        "AutoModel.from_pretrained('bert-base-uncased')\n"
    )
    _write(tmp_path, files)
    _write_manifest_file(tmp_path)
    result = run_check(tmp_path)

    refreshed = scaffold_manifest(result.evidence, refresh=True)

    assert refreshed.paper.title == "Demo Paper"
    assert refreshed.environment.hardware == "1x A100"
    assert refreshed.claims[0].text == "Accuracy of 92.1"
    assert refreshed.claims[0].produced_by.command == "bash run.sh"
    assert refreshed.datasets[0].checksum == "sha256:abc"
    assert refreshed.remotes[0].revision == "8" * 40
    assert any("bert-base-uncased" in remote.call for remote in refreshed.remotes)


def test_claim_graph_with_manifest(tmp_path):
    files = dict(WELL_FORMED)
    files["results/eval.csv"] = "epoch,accuracy\n1,92.07\n"
    _write(tmp_path, files)
    _write_manifest_file(tmp_path)
    result = run_check(tmp_path)
    assert result.graph.from_manifest
    trail = result.graph.trails[0]
    assert trail.status is TrailStatus.PARTIAL
    assert not trail.inferred
    labels = {entry.label for entry in trail.entries}
    assert {"metric", "config", "log", "seeds"} <= labels
    metric_entry = next(e for e in trail.entries if e.label == "metric")
    assert metric_entry.resolved is True  # 92.07 rounds to the claimed 92.1
    assert next(e for e in trail.entries if e.label == "seeds").resolved is None
    assert next(e for e in trail.entries if e.label == "commit").resolved is False


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
    assert trail.status is not TrailStatus.SUPPORTED


def test_claim_graph_does_not_treat_one_existing_path_as_supported(tmp_path):
    files = dict(WELL_FORMED)
    files["results/eval.csv"] = "epoch,accuracy\n1,92.1\n"
    _write(tmp_path, files)
    manifest = dict(_MANIFEST)
    manifest["claims"] = [
        {
            "id": "C1",
            "produced_by": {"config": "configs/main.yaml"},
        }
    ]
    (tmp_path / ".adduce").mkdir(exist_ok=True)
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest),
        encoding="utf-8",
    )

    trail = run_check(tmp_path).graph.trails[0]

    assert trail.status is TrailStatus.PARTIAL


def test_claim_graph_and_json_include_every_manifest_claim(tmp_path):
    _write(tmp_path, dict(WELL_FORMED))
    manifest = dict(_MANIFEST)
    manifest["claims"] = [
        {"id": f"C{index}", "text": f"Claim {index}"}
        for index in range(1, 13)
    ]
    (tmp_path / ".adduce").mkdir(exist_ok=True)
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest),
        encoding="utf-8",
    )

    result = run_check(tmp_path)
    payload = json.loads(render_json(result))

    assert [trail.claim.id for trail in result.graph.trails] == [
        f"C{index}" for index in range(1, 13)
    ]
    assert [claim["id"] for claim in payload["claims"]] == [
        f"C{index}" for index in range(1, 13)
    ]


def test_draft_manifest_claim_is_inferred(tmp_path):
    files = dict(WELL_FORMED)
    files["results/eval.csv"] = "epoch,accuracy\n1,92.07\n"
    _write(tmp_path, files)
    manifest = dict(_MANIFEST)
    manifest["claims"] = [dict(_MANIFEST["claims"][0], status="draft")]
    (tmp_path / ".adduce").mkdir(exist_ok=True)
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )

    trail = run_check(tmp_path).graph.trails[0]

    assert trail.inferred
    assert trail.status is TrailStatus.PARTIAL


def test_inferred_placeholder_is_never_reported_as_supported(tmp_path):
    files = dict(WELL_FORMED)
    files["README.md"] = "# Demo\n\n## Results\n\n| Accuracy |\n|---|\n| 92.1 |\n"
    _write(tmp_path, files)

    result = run_check(tmp_path)

    assert result.graph.trails
    assert all(trail.inferred for trail in result.graph.trails)
    assert all(trail.status is not TrailStatus.SUPPORTED for trail in result.graph.trails)


def test_claim_graph_metric_source_matches_closest_value(tmp_path):
    files = dict(WELL_FORMED)
    files["results/main.csv"] = "epoch,accuracy\n1,92.07\n"
    files["results/other.csv"] = "epoch,accuracy\n1,10.0\n"
    _write(tmp_path, files)
    manifest = dict(_MANIFEST)
    manifest["claims"] = [
        {
            "id": "C1",
            "metric": "accuracy",
            "value": 92.1,
            "produced_by": {"log": "results/main.csv"},
        }
    ]
    (tmp_path / ".adduce").mkdir(exist_ok=True)
    (tmp_path / ".adduce" / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )

    metric = next(
        entry for entry in run_check(tmp_path).graph.trails[0].entries if entry.label == "metric"
    )

    assert "results/main.csv:accuracy" in metric.value
    assert "results/other.csv" not in metric.value


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
    assert functional.eligible  # repository-side prerequisites pass
    assert functional.manual_review
    assert any("execution" in item for item in functional.manual_review)
