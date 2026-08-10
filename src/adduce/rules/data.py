"""Data: can someone else obtain, verify, and correctly use the data?"""

from __future__ import annotations

from ..evidence import Evidence
from ..evidence.data import DATA_DIRECTORY_NAMES
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status

_ML_FRAMEWORKS = frozenset(
    {"torch", "tensorflow", "sklearn", "jax", "lightning", "transformers", "xgboost", "lightgbm", "pandas"}
)


def _data_repo(repo: Repo) -> bool:
    return repo.frameworks.uses_any(_ML_FRAMEWORKS)


def _uses_dataset_loaders(ev: Evidence) -> bool:
    return ev.py.calls_any("datasets.load_dataset", "sklearn.datasets.fetch_openml") or any(
        site.qualname.startswith("torchvision.datasets.")
        for module in ev.py.modules
        for site in module.calls
    )


def _reference_bound_checksum_files(ev: Evidence) -> list[str]:
    """Files containing a download with a visibly bound checksum check."""
    return sorted(
        {
            reference.file
            for reference in ev.remote.references
            if reference.kind in {"url", "gdrive", "bucket"}
            and reference.pin_detail == "checksum"
        }
    )


class DataProvenanceRule(Rule):
    id = "R-DATA-001"
    category = Category.DATA
    title = "Data-source documentation signals"
    rationale = (
        "Documenting each dataset's origin helps reviewers assess whether the inputs can be obtained "
        "and interpreted."
    )
    weight = 4

    def applies_to(self, repo: Repo) -> bool:
        return _data_repo(repo)

    def evaluate(self, ev: Evidence) -> Finding:
        signals: list[str] = []
        if ev.manifest.datasets:
            signals.append(f"manifest declares {len(ev.manifest.datasets)} dataset(s)")
        if ev.data.dataset_urls:
            signals.append("dataset host or DOI referenced in the README")
        if ev.docs.has_section("data"):
            signals.append("README data section")
        if _uses_dataset_loaders(ev):
            signals.append("programmatic dataset loading (torchvision/huggingface/sklearn)")
        if signals:
            status = Status.PASS if len(signals) >= 2 or ev.manifest.datasets else Status.PARTIAL
            return self.finding(
                status,
                confidence=0.75,
                message="Data-source documentation signal(s) detected: " + "; ".join(signals) + ".",
                remediation="" if status is Status.PASS else "Name the dataset source (ideally a DOI) explicitly in the README data section.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="No data-source documentation signal detected in a README data section, dataset link, or manifest entry.",
            remediation="Add a README data section naming each dataset, its source (DOI where possible), and its license.",
        )


class DownloadPathRule(Rule):
    id = "R-DATA-002"
    category = Category.DATA
    title = "Scripted or documented data-acquisition path"
    rationale = "A download script (or clearly documented manual path) makes data access mechanical rather than archaeological."
    weight = 4

    def applies_to(self, repo: Repo) -> bool:
        return _data_repo(repo)

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.data.download_scripts or ev.data.uses_dvc:
            what = (
                f"download script(s): {', '.join(ev.data.download_scripts[:3])}"
                if ev.data.download_scripts
                else "DVC pipeline"
            )
            return self.finding(Status.PASS, confidence=0.85, message=f"Scripted data acquisition: {what}.")
        if _uses_dataset_loaders(ev):
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message="Data is acquired programmatically at run time (dataset-library loaders).",
            )
        if ev.data.dataset_urls or ev.docs.has_section("data"):
            return self.finding(
                Status.PARTIAL,
                confidence=0.7,
                message="Data acquisition is documented but not scripted.",
                remediation="Add a scripts/download_data.sh (with checksums) so acquisition is one command.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="No download script, DVC stage, dataset-library loader, or documented manual path detected.",
            remediation="Script the download, or document the exact manual steps and where files must be placed.",
        )


class DataIntegrityRule(Rule):
    id = "R-DATA-003"
    category = Category.DATA
    title = "Dataset-integrity evidence"
    rationale = (
        "Dataset-specific checksums and content-addressed metadata make upstream changes "
        "detectable. Static evidence shows that a verification path exists, not that it ran."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return _data_repo(repo)

    def evaluate(self, ev: Evidence) -> Finding:
        if ev.data.uses_dvc:
            return self.finding(
                Status.PASS,
                confidence=0.85,
                message="DVC metadata or pipeline files provide a content-addressed data-integrity signal.",
            )
        bound_files = _reference_bound_checksum_files(ev)
        if bound_files:
            return self.finding(
                Status.PASS,
                confidence=0.85,
                message=(
                    "Checksum verification is visibly bound to a data download in: "
                    + ", ".join(bound_files[:3])
                    + "."
                ),
            )
        if any(d.checksum for d in ev.manifest.datasets):
            return self.finding(
                Status.PARTIAL,
                confidence=0.8,
                message=(
                    "The manifest declares dataset checksum(s), but the static scan did not "
                    "confirm that the acquisition path verifies them."
                ),
                remediation="Verify each declared checksum in the scripted data-acquisition path.",
            )
        if ev.data.checksum_files:
            return self.finding(
                Status.PARTIAL,
                confidence=0.65,
                message=(
                    "Checksum file(s) are present, but their coverage is not visibly linked "
                    "to a detected data download: "
                    + ", ".join(ev.data.checksum_files[:3])
                    + "."
                ),
                remediation="Verify the checksum file in the download script and name the downloaded target explicitly.",
            )
        if ev.data.uses_hash_verification and ev.data.download_scripts:
            return self.finding(
                Status.PARTIAL,
                confidence=0.5,
                message="hashlib is used alongside a download script, which suggests but does not confirm checksum verification.",
                remediation="Commit an explicit SHA256SUMS file and verify it in the download script.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message="No checksums or content-addressed data tracking detected.",
            remediation="Record SHA-256 checksums of the datasets and verify them after download.",
        )


class CommittedBinariesRule(Rule):
    id = "R-DATA-004"
    category = Category.DATA
    title = "Large binaries not committed raw into git"
    rationale = (
        "Weights or datasets committed straight into git usually mean the 'reproduction' "
        "ships outputs rather than reruns them, and they bloat every clone."
    )
    weight = 4

    def evaluate(self, ev: Evidence) -> Finding:
        binaries = ev.data.large_binaries
        if not binaries:
            return self.finding(Status.PASS, confidence=0.9, message="No large binaries or serialized artifacts committed.")
        untracked = ev.data.untracked_binaries
        if not untracked:
            return self.finding(
                Status.PASS, confidence=0.85, message=f"{len(binaries)} large binary file(s) present, all covered by Git LFS."
            )
        total_mb = sum(b.size for b in untracked) / (1024 * 1024)
        return self.finding(
            Status.PARTIAL if len(untracked) < len(binaries) else Status.FAIL,
            confidence=0.85,
            message=f"{len(untracked)} large binary file(s) ({total_mb:.0f} MiB) committed without Git LFS.",
            remediation=(
                "Move weights and datasets out of git: publish to an archival host (Zenodo, Hugging Face) "
                "and download at setup, or track with Git LFS / DVC."
            ),
            locations=[Location(b.path) for b in sorted(untracked, key=lambda b: -b.size)[:5]],
        )


class DataFrictionRule(Rule):
    id = "R-DATA-005"
    category = Category.DATA
    title = "Data-access path heuristic"
    rationale = (
        "A documented, scripted, and integrity-checked acquisition path gives reviewers stronger "
        "static evidence that the intended data can be identified and retrieved."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return _data_repo(repo)

    def evaluate(self, ev: Evidence) -> Finding:
        scripted = bool(ev.data.download_scripts) or ev.data.uses_dvc or _uses_dataset_loaders(ev)
        checksummed = (
            ev.data.uses_dvc
            or bool(_reference_bound_checksum_files(ev))
            or any(d.checksum for d in ev.manifest.datasets)
        )
        documented = ev.data.dataset_urls or ev.docs.has_section("data") or bool(ev.manifest.datasets)
        gated = bool(ev.remote.by_kind("gdrive")) or bool(ev.remote.by_kind("bucket"))

        if scripted and checksummed:
            grade, status = "A (scripted acquisition with dataset-specific integrity evidence)", Status.PASS
        elif scripted:
            grade, status = "B (scripted acquisition without dataset-specific integrity evidence)", Status.PASS
        elif documented and checksummed:
            grade, status = "B (documented acquisition with dataset-specific integrity evidence)", Status.PASS
        elif documented and gated:
            grade, status = "C (documented path on a host that may apply access controls)", Status.PARTIAL
        elif documented:
            grade, status = "C (documented manual path without detected integrity checks)", Status.PARTIAL
        elif gated:
            grade, status = "D (hosted path detected without acquisition documentation)", Status.FAIL
        else:
            grade, status = "E (no data-source or acquisition signal detected)", Status.FAIL
        return self.finding(
            status,
            confidence=0.7,
            message=f"Heuristic data-access grade: {grade}.",
            remediation=(
                ""
                if status is Status.PASS
                else "Strengthen the acquisition path with explicit instructions, automation, integrity checks, and access requirements."
            ),
        )


class RawProcessedRule(Rule):
    id = "R-DATA-006"
    category = Category.DATA
    title = "Raw vs processed data distinguished"
    rationale = (
        "When raw inputs and derived artifacts share a directory, nobody can tell what is "
        "input and what is output — a standard data-engineering convention solves it."
    )
    weight = 1

    def applies_to(self, repo: Repo) -> bool:
        return _data_repo(repo)

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.data.top_level_directories & DATA_DIRECTORY_NAMES:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No committed data/ directory to organise."
            )
        subdirs = ev.data.data_subdirectories
        markers = {"raw", "processed", "interim", "external", "splits", "prepared", "clean", "cleaned"}
        if subdirs & markers:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message="data/ distinguishes raw from derived content (" + ", ".join(sorted(subdirs & markers)) + ").",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message="A data directory exists but does not separate raw from processed content.",
            remediation="Adopt data/raw and data/processed (plus data/splits) so inputs and derivations are unambiguous.",
        )
