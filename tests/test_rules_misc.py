"""Docs, data, execution, versioning, and licensing rules."""

from __future__ import annotations

from adduce.rules.base import Status
from adduce.rules.data import (
    CommittedBinariesRule,
    DataFrictionRule,
    DataIntegrityRule,
    DataProvenanceRule,
    DownloadPathRule,
)
from adduce.rules.docs import ExpectedResultsRule, ReadmeSectionsRule
from adduce.rules.exec_ import EntrypointRule, RunnerRule
from adduce.rules.licensing import CitationRule, LicenseRule
from adduce.rules.versioning import GitRepositoryRule

_README_FULL = """# Demo

## Installation

```bash
pip install -r requirements.txt
```

## Reproducing results

```bash
python train.py --seed 0
```

## Expected results

| Metric | Value |
|---|---|
| Accuracy | 92.1 |

## Hardware

Trained on 1x NVIDIA A100 (80 GB), ~3 hours.

## Data

Download from https://zenodo.org/record/1234567 via `scripts/download_data.sh`.
"""


def test_full_readme_passes_doc_rules(make_evidence):
    ev = make_evidence({"README.md": _README_FULL, "train.py": "pass\n"})
    assert ReadmeSectionsRule().evaluate(ev).status is Status.PASS
    assert ExpectedResultsRule().evaluate(ev).status is Status.PASS


def test_missing_readme_fails_doc_rules(make_evidence):
    ev = make_evidence({"train.py": "pass\n"})
    assert ReadmeSectionsRule().evaluate(ev).status is Status.FAIL


def test_partial_readme_lists_missing_sections(make_evidence):
    ev = make_evidence(
        {"README.md": "# Demo\n\n## Installation\n\npip install .\n", "train.py": "pass\n"}
    )
    finding = ReadmeSectionsRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "usage" in finding.message


def test_entrypoint_conventional_file(make_evidence):
    ev = make_evidence({"train.py": "print('x')\n"})
    assert EntrypointRule().evaluate(ev).status is Status.PASS


def test_entrypoint_main_guard_only_is_partial(make_evidence):
    ev = make_evidence({"script.py": "if __name__ == '__main__':\n    pass\n"})
    assert EntrypointRule().evaluate(ev).status is Status.PARTIAL


def test_runner_with_script_passes(make_evidence):
    ev = make_evidence(
        {"README.md": _README_FULL, "run.sh": "#!/bin/bash\npython train.py\n", "train.py": "pass\n"}
    )
    assert RunnerRule().evaluate(ev).status is Status.PASS


def test_runner_docs_only_is_partial(make_evidence):
    ev = make_evidence({"README.md": _README_FULL, "train.py": "pass\n"})
    assert RunnerRule().evaluate(ev).status is Status.PARTIAL


def test_committed_weights_without_lfs_flagged(make_evidence):
    ev = make_evidence({"models/best.ckpt": "fake weights", "train.py": "pass\n"})
    finding = CommittedBinariesRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert finding.locations[0].path == "models/best.ckpt"


def test_lfs_covered_weights_pass(make_evidence):
    ev = make_evidence(
        {
            ".gitattributes": "*.ckpt filter=lfs diff=lfs merge=lfs -text\n",
            "models/best.ckpt": "x" * 2048,
            "train.py": "pass\n",
        }
    )
    assert CommittedBinariesRule().evaluate(ev).status is Status.PASS


def test_lfs_pointer_file_recognised(make_evidence):
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123456789\n"
    ev = make_evidence({"models/big.pt": pointer, "train.py": "pass\n"})
    assert CommittedBinariesRule().evaluate(ev).status is Status.PASS


def test_download_path_from_script(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "scripts/download_data.sh": "curl -O https://example.org/data.tar\n",
            "train.py": "import torch\n",
        }
    )
    assert DownloadPathRule().evaluate(ev).status is Status.PASS


def test_data_provenance_fails_without_any_path(make_evidence):
    ev = make_evidence({"requirements.txt": "torch==2.1.0\n", "train.py": "import torch\n"})
    assert DataProvenanceRule().evaluate(ev).status is Status.FAIL


def test_unlinked_checksum_file_is_only_partial_integrity_evidence(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "scripts/download_data.sh": "curl https://example.org/data.tar -o data.tar\n",
            "SHA256SUMS.txt": "0" * 64 + "  unrelated-model.bin\n",
            "train.py": "import torch\n",
        }
    )

    integrity = DataIntegrityRule().evaluate(ev)
    friction = DataFrictionRule().evaluate(ev)

    assert integrity.status is Status.PARTIAL
    assert "not visibly linked" in integrity.message
    assert friction.status is Status.PASS
    assert "B (scripted acquisition without dataset-specific integrity evidence)" in friction.message


def test_download_bound_checksum_is_strong_static_integrity_evidence(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "scripts/download_data.sh": (
                "curl https://example.org/data.tar -o data.tar\n"
                "sha256sum -c data.tar.sha256\n"
            ),
            "train.py": "import torch\n",
        }
    )

    integrity = DataIntegrityRule().evaluate(ev)
    friction = DataFrictionRule().evaluate(ev)

    assert integrity.status is Status.PASS
    assert "visibly bound" in integrity.message
    assert friction.status is Status.PASS
    assert "A (scripted acquisition with dataset-specific integrity evidence)" in friction.message


def test_manifest_checksum_does_not_imply_static_verification(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            ".adduce/manifest.yaml": (
                "schema: adduce/1\n"
                "datasets:\n"
                "  - id: demo\n"
                "    source: https://example.org/data.tar\n"
                "    checksum: sha256:abc\n"
            ),
            "train.py": "import torch\n",
        }
    )

    finding = DataIntegrityRule().evaluate(ev)

    assert finding.status is Status.PARTIAL
    assert "declares dataset checksum" in finding.message
    assert "did not confirm" in finding.message


def test_license_and_citation(make_evidence):
    ev = make_evidence(
        {"LICENSE": "MIT", "CITATION.cff": "cff-version: 1.2.0\n", "train.py": "pass\n"}
    )
    assert LicenseRule().evaluate(ev).status is Status.PASS
    assert CitationRule().evaluate(ev).status is Status.PASS


def test_nested_citation_does_not_satisfy_repository_citation(make_evidence):
    ev = make_evidence(
        {"metadata/CITATION.cff": "cff-version: 1.2.0\n", "train.py": "pass\n"}
    )
    assert ev.docs.citation_file is None
    assert CitationRule().evaluate(ev).status is Status.FAIL


def test_generated_submission_directory_is_excluded_from_scan(make_evidence):
    ev = make_evidence(
        {
            "adduce-submission/CITATION.cff": "cff-version: 1.2.0\n",
            "train.py": "pass\n",
        }
    )
    assert all("adduce-submission" not in file.path.parts for file in ev.repo.files)
    assert CitationRule().evaluate(ev).status is Status.FAIL


def test_bibtex_without_cff_is_partial(make_evidence):
    ev = make_evidence(
        {"README.md": "# X\n```\n@inproceedings{x}\n```\n", "train.py": "pass\n"}
    )
    assert CitationRule().evaluate(ev).status is Status.PARTIAL


def test_non_git_directory_fails_versioning(make_evidence):
    ev = make_evidence({"train.py": "pass\n"})
    assert GitRepositoryRule().evaluate(ev).status is Status.FAIL
