"""Bounded, immutable generation-safety evidence for the corpus sentinels."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import corpus.scripts.audit_sentinel_generation as generation
import pytest
from corpus.scripts.clone_repos import repository_tree_sha256

GENERATED_AT = "2026-07-13T20:00:00+00:00"
COMMIT = "a" * 40


def _artifact_sha(text: str) -> str:
    return hashlib.sha256((text.rstrip("\n") + "\n").encode()).hexdigest()


def _raw_payload() -> dict[str, Any]:
    return {
        "tool": {"name": "adduce", "version": "0.1.2.dev0"},
        "repository": {
            "root": "/relocatable/input",
            "commit": COMMIT,
            "frameworks": ["python"],
            "files_scanned": 1,
            "input_file_count": 1,
            "input_byte_count": 11,
        },
        "reviewer_time": {
            "low_minutes": 1,
            "high_minutes": 2,
            "bucket": "under 5 minutes",
            "unknown": False,
            "factors": [],
        },
        "claims": [],
        "total": 100.0,
        "tier": "Gold",
        "profile": "default",
        "categories": [],
        "findings": [],
        "corpus_execution": {"resource_observations": {"peak_rss_bytes": 1}},
    }


def _context(tmp_path: Path) -> generation.GenerationContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    run = tmp_path / "run"
    clones = tmp_path / "clones"
    run.mkdir()
    clones.mkdir()
    repositories: dict[str, generation.RepositoryInput] = {}
    for repo_id in generation.SENTINELS:
        clone = clones / repo_id
        clone.mkdir()
        (clone / "README.md").write_text("# Evidence\n", encoding="utf-8")
        raw = run / f"{repo_id}.json"
        raw.write_text(json.dumps(_raw_payload()) + "\n", encoding="utf-8")
        repositories[repo_id] = generation.RepositoryInput(
            repo_id=repo_id,
            clone=clone,
            commit=COMMIT,
            worktree_sha256=repository_tree_sha256(clone),
            raw_json=raw,
            raw_json_sha256=generation.sha256_file(raw),
        )
    return generation.GenerationContext(
        run=run,
        clones=clones,
        metadata={
            "run_id": "fixture-run",
            "adduce_version": "0.1.2.dev0",
            "adduce_source_tree_sha256": "1" * 64,
            "clone_manifest_sha256": "2" * 64,
        },
        run_meta_sha256="3" * 64,
        script_sha256="4" * 64,
        schema_sha256="5" * 64,
        checker_sha256="6" * 64,
        repositories=repositories,
    )


def _entry(answer: str = "yes", confidence: float = 0.95) -> dict[str, Any]:
    evidence = []
    if answer in generation.AFFIRMATIVE_ANSWERS:
        evidence = [
            {
                "kind": "R-DOC-001",
                "path": "README.md",
                "line": 1,
                "confidence": confidence,
                "strength": "direct",
            }
        ]
    return {
        "item_id": "documented",
        "question": "Is the repository documented?",
        "answer": answer,
        "evidence": evidence,
        "searched": ["R-DOC-001"],
        "missing": [],
        "conflicts": [],
    }


def _record(
    context: generation.GenerationContext,
    repository: generation.RepositoryInput,
    artifact_name: str,
    text: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    entries = [entry]
    return {
        "artifact_path": artifact_name,
        "artifact_sha256": _artifact_sha(text),
        "provenance": generation._expected_provenance(
            context=context,
            repository=repository,
            generated_at=GENERATED_AT,
            artifact_name=artifact_name,
        ),
        "generated_text_policy": "evidence_only",
        "counts": generation._expected_counts(entries),
        "entries": entries,
    }


def _ledger_fixture(
    context: generation.GenerationContext,
    repository: generation.RepositoryInput,
    *,
    checklist_entry: dict[str, Any] | None = None,
    checklist_suffix: str = "",
) -> tuple[dict[str, str], dict[str, Any]]:
    checklist_entry = checklist_entry or _entry()
    checklist_text = (
        "# Checklist\n\n"
        f"**Answer:** {generation.ANSWER_TEXT[checklist_entry['answer']]}\n"
        f"{checklist_suffix}"
    )
    appendix_text = "# Artifact Appendix\n\nStatic draft.\n"
    unknown = _entry("unknown")
    artifacts = {
        "checklist-neurips.md": checklist_text,
        "artifact_appendix.md": appendix_text,
    }
    records = {
        "checklist-neurips.md": _record(
            context, repository, "checklist-neurips.md", checklist_text, checklist_entry
        ),
        "artifact_appendix.md": _record(
            context, repository, "artifact_appendix.md", appendix_text, unknown
        ),
    }
    return artifacts, records


def _make_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, generation.GenerationContext]:
    context = _context(tmp_path)
    monkeypatch.setattr(generation, "_git_head", lambda _path: COMMIT)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    repository_records = []
    artifact_paths: list[str] = []
    yes_total = 0
    partial_total = 0
    for repo_id in generation.SENTINELS:
        repository = context.repositories[repo_id]
        artifacts, records = _ledger_fixture(context, repository)
        repo_root = bundle / repo_id
        repo_root.mkdir()
        for name, text in artifacts.items():
            (repo_root / name).write_text(text.rstrip("\n") + "\n", encoding="utf-8")
            artifact_paths.append(f"{repo_id}/{name}")
        (repo_root / generation.LEDGER_NAME).write_text(
            json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        artifact_paths.append(f"{repo_id}/{generation.LEDGER_NAME}")
        audited, failures = generation._audit_ledger_bundle(
            repository=repository,
            context=context,
            generated_at=GENERATED_AT,
            artifact_texts=artifacts,
            ledger_records=records,
        )
        yes, partial = generation._answer_totals(records)
        yes_total += yes
        partial_total += partial
        raw_projection = generation._projection_sha256(_raw_payload())
        repo_paths = {f"{repo_id}/{name}" for name in (*generation.ARTIFACT_NAMES, generation.LEDGER_NAME)}
        repo_artifacts = sorted(
            (
                generation._artifact_record(bundle, path)
                for path in artifact_paths
                if path in repo_paths
            ),
            key=lambda record: record["path"],
        )
        repository_records.append(
            {
                "id": repo_id,
                "commit": COMMIT,
                "worktree_sha256": repository.worktree_sha256,
                "raw_json_sha256": repository.raw_json_sha256,
                "raw_projection_sha256": raw_projection,
                "rerun_projection_sha256": raw_projection,
                "raw_scan_match": True,
                "artifacts": repo_artifacts,
                "affirmative_entries": audited,
                "failures": failures,
            }
        )
    artifact_records = sorted(
        (generation._artifact_record(bundle, path) for path in artifact_paths),
        key=lambda record: record["path"],
    )
    manifest = {
        "schema_version": generation.SCHEMA_VERSION,
        "procedure": generation.PROCEDURE,
        "generated_at": GENERATED_AT,
        "result": "pass",
        "sentinels": list(generation.SENTINELS),
        "generation_policy": generation._GENERATION_POLICY,
        "source": generation._manifest_source(context),
        "repositories": repository_records,
        "artifacts": artifact_records,
        "summary": {
            "repositories": 3,
            "generated_artifacts": 6,
            "affirmative_entries": yes_total + partial_total,
            "yes_entries": yes_total,
            "partial_entries": partial_total,
            "failures": [],
        },
    }
    (bundle / generation.MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (bundle / generation.SUCCESS_MARKER).write_text(
        "generation audit passed\n", encoding="utf-8"
    )
    return bundle, context


def test_affirmative_ledger_audit_accepts_strict_direct_evidence(tmp_path):
    context = _context(tmp_path)
    repository = context.repositories["frl"]
    artifacts, records = _ledger_fixture(context, repository)

    audited, failures = generation._audit_ledger_bundle(
        repository=repository,
        context=context,
        generated_at=GENERATED_AT,
        artifact_texts=artifacts,
        ledger_records=records,
    )

    assert failures == []
    assert audited == [
        {
            "artifact_path": "checklist-neurips.md",
            "item_id": "documented",
            "answer": "yes",
            "evidence_count": 1,
            "strengths": ["direct"],
            "result": "pass",
        }
    ]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({**_entry("partial"), "evidence": []}, "partial answer has no evidence"),
        (_entry("yes", confidence=0.89), "yes answer lacks strict direct"),
    ],
)
def test_affirmative_ledger_audit_records_unbacked_and_weak_answers(
    tmp_path, entry, expected
):
    context = _context(tmp_path)
    repository = context.repositories["frl"]
    artifacts, records = _ledger_fixture(context, repository, checklist_entry=entry)

    audited, failures = generation._audit_ledger_bundle(
        repository=repository,
        context=context,
        generated_at=GENERATED_AT,
        artifact_texts=artifacts,
        ledger_records=records,
    )

    assert audited == []
    assert any(expected in failure for failure in failures)


def test_static_execution_language_requires_dynamic_evidence(tmp_path):
    context = _context(tmp_path)
    repository = context.repositories["frl"]
    artifacts, records = _ledger_fixture(
        context, repository, checklist_suffix="\nAll results were reproduced.\n"
    )
    records["checklist-neurips.md"]["artifact_sha256"] = _artifact_sha(
        artifacts["checklist-neurips.md"]
    )

    _, failures = generation._audit_ledger_bundle(
        repository=repository,
        context=context,
        generated_at=GENERATED_AT,
        artifact_texts=artifacts,
        ledger_records=records,
    )

    assert any("static draft implies execution" in failure for failure in failures)


@pytest.mark.parametrize(
    "payload",
    [b'{"schema_version": 1, "schema_version": 1}', b'{"value": NaN}'],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(payload):
    with pytest.raises(generation.GenerationAuditError):
        generation._load_json_bytes(payload, "fixture")


def test_worker_suppresses_only_repository_syntax_warnings() -> None:
    environment = generation._minimal_worker_environment()

    assert environment["PYTHONWARNINGS"] == "ignore::SyntaxWarning"


def test_bundle_validation_rejects_missing_and_extra_files(tmp_path, monkeypatch):
    bundle, context = _make_bundle(tmp_path, monkeypatch)
    assert generation.validate_bundle(bundle, context)["result"] == "pass"

    (bundle / "frl" / "checklist-neurips.md").unlink()
    with pytest.raises(generation.GenerationAuditError, match="file set mismatch"):
        generation.validate_bundle(bundle, context)

    bundle, context = _make_bundle(tmp_path / "second", monkeypatch)
    (bundle / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(generation.GenerationAuditError, match="file set mismatch"):
        generation.validate_bundle(bundle, context)


@pytest.mark.parametrize("field", ["run_id", "adduce_source_tree_sha256"])
def test_bundle_validation_rejects_run_and_source_drift(tmp_path, monkeypatch, field):
    bundle, context = _make_bundle(tmp_path, monkeypatch)
    manifest_path = bundle / generation.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"][field] = "drift" if field == "run_id" else "f" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(generation.GenerationAuditError, match="source or run binding drifted"):
        generation.validate_bundle(bundle, context)


def test_bundle_validation_rejects_clone_drift(tmp_path, monkeypatch):
    bundle, context = _make_bundle(tmp_path, monkeypatch)
    (context.repositories["frl"].clone / "new.py").write_text("pass\n", encoding="utf-8")

    with pytest.raises(generation.GenerationAuditError, match="worktree drift"):
        generation.validate_bundle(bundle, context)
