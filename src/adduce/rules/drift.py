"""Paper ↔ artifact consistency (drift).

Compares the hyperparameters and facts the paper states against what the
repository declares, with an explicit authority ranking: a materialised run
config (Hydra output, W&B, MLflow) outranks a checked-in config file, which
outranks an argparse/dataclass default — a default alone is weak evidence of
what was actually run.

Integers compare exactly; floats with rounding-awareness (a paper's 0.814
matches a logged 0.8137). Everything here is probabilistic and reported with
confidence; nothing auto-edits the ``.tex``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..evidence import Evidence
from ..model import Repo
from ..naming import canonical_hyperparameter
from .base import Category, Finding, Location, Rule, Status


def values_match(paper: float, code: float) -> bool:
    """Rounding-aware comparison: the paper value is allowed to be a rounded
    representation of the code value."""
    if paper == code:
        return True
    if code != 0 and abs(paper - code) / abs(code) < 1e-6:
        return True
    text = f"{paper:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text and text.split(".")[1] else 0
    return abs(code - paper) <= 0.5 * 10 ** (-decimals) + 1e-12


@dataclass
class _CodeValue:
    value: float
    source: str        # file path
    key: str
    authority: int     # 3 = materialised run config, 2 = config file, 1 = default


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _code_values(ev: Evidence) -> dict[str, list[_CodeValue]]:
    """All code-side hyperparameter values, ranked by authority."""
    found: dict[str, list[_CodeValue]] = {}

    def add(name: str, value: Any, source: str, key: str, authority: int) -> None:
        number = _numeric(value)
        if number is not None:
            found.setdefault(name, []).append(_CodeValue(number, source, key, authority))

    for name, entries in ev.runs.hyperparameters().items():
        for value, path, key in entries:
            add(name, value, path, key, authority=3)
    for name, entries in ev.config.hyperparameters().items():
        for value, path, key in entries:
            add(name, value, path, key, authority=2)
    for arg in [*ev.py.cli_args, *ev.py.dataclass_defaults]:
        canonical = canonical_hyperparameter(arg.name)
        if canonical:
            add(canonical, arg.default, arg.file, arg.name, authority=1)
    return found


class HyperparameterDriftRule(Rule):
    id = "R-DRIFT-001"
    category = Category.DRIFT
    title = "Paper hyperparameter differs from the authoritative code value"
    rationale = (
        "Configs get tuned after the paper freezes; a stated learning rate that no config "
        "contains is the classic camera-ready drift."
    )
    weight = 5

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        paper_values = ev.latex.hyperparameter_values()
        if not paper_values:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No hyperparameter statements extracted from the paper."
            )
        code_values = _code_values(ev)
        drifted: list[tuple[str, float, _CodeValue]] = []
        checked = 0
        for name, statements in paper_values.items():
            candidates = code_values.get(name)
            if not candidates:
                continue
            top_authority = max(c.authority for c in candidates)
            authoritative = [c for c in candidates if c.authority == top_authority]
            for statement in statements:
                checked += 1
                if not any(values_match(statement.value, c.value) for c in authoritative):
                    drifted.append((name, statement.value, authoritative[0]))
        if checked == 0:
            return self.finding(
                Status.UNKNOWN,
                confidence=0.5,
                message="Paper hyperparameters were extracted but none could be matched to code-side values.",
            )
        if not drifted:
            return self.finding(
                Status.PASS,
                confidence=0.65,
                message=f"All {checked} paper hyperparameter statement(s) with code counterparts agree "
                "with the authoritative values.",
            )
        details = "; ".join(
            f"{name}: paper says {paper_value:g}, {code.source} has {code.value:g} ({code.key})"
            for name, paper_value, code in drifted[:4]
        )
        return self.finding(
            Status.FAIL if any(code.authority >= 2 for _, _, code in drifted) else Status.PARTIAL,
            confidence=0.7,
            message=f"{len(drifted)} hyperparameter(s) drift between paper and code: {details}.",
            remediation="Update the paper or the config so they agree, and record the authoritative config in the manifest.",
            locations=[Location(code.source) for _, _, code in drifted[:5]],
        )


class AmbiguousConfigRule(Rule):
    id = "R-DRIFT-002"
    category = Category.DRIFT
    title = "Multiple candidate configs; cannot resolve which backs the paper"
    rationale = (
        "When several configs carry different values for the same hyperparameter and nothing "
        "says which run produced the paper, the reader cannot resolve it either. Ambiguity, "
        "not necessarily error."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        paper_values = ev.latex.hyperparameter_values()
        if not paper_values:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No hyperparameter statements extracted from the paper.")
        if any(c.produced_by.config for c in ev.manifest.claims):
            return self.finding(
                Status.PASS, confidence=0.85, message="The manifest resolves which config backs each claim."
            )
        code_values = _code_values(ev)
        ambiguous: list[str] = []
        for name in paper_values:
            candidates = code_values.get(name, [])
            config_level = [c for c in candidates if c.authority == 2]
            distinct = {c.value for c in config_level}
            if len(distinct) > 1 and not any(c.authority == 3 for c in candidates):
                ambiguous.append(f"{name} ({len(distinct)} distinct config values)")
        if not ambiguous:
            return self.finding(Status.PASS, confidence=0.6, message="No unresolved multi-config ambiguity detected.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message="Cannot resolve which config backs the paper for: " + ", ".join(ambiguous[:4]) + ".",
            remediation="Record produced_by.config per claim in .adduce/manifest.yaml, or name the config in the paper/README.",
        )


class MissingHyperparameterRule(Rule):
    id = "R-DRIFT-003"
    category = Category.DRIFT
    title = "Hyperparameter reported in the paper not found in code"
    rationale = "A stated setting with no code counterpart cannot be verified or reproduced."
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        paper_values = ev.latex.hyperparameter_values()
        if not paper_values:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No hyperparameter statements extracted from the paper.")
        code_values = _code_values(ev)
        missing = [name for name in paper_values if name not in code_values]
        if not missing:
            return self.finding(
                Status.PASS,
                confidence=0.65,
                message=f"Every paper-stated hyperparameter ({', '.join(sorted(paper_values))}) has a code counterpart.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.6,
            message="Paper states hyperparameter(s) with no detected code counterpart: " + ", ".join(sorted(missing)) + ".",
            remediation="Ensure each stated setting exists in a committed config (naming may also differ beyond the synonym map).",
        )


class DatasetDriftRule(Rule):
    id = "R-DRIFT-004"
    category = Category.DRIFT
    title = "Dataset named in the paper not found in code or configs"
    rationale = "The dataset the paper names and the dataset the code loads must be the same thing."
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        mentioned = ev.latex.datasets_mentioned
        if not mentioned:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No recognised dataset names extracted from the paper.")

        code_tokens: set[str] = set()
        for site in ev.py.call_sites_terminal("load_dataset"):
            if site.first_arg:
                code_tokens.add(site.first_arg.lower())
        for module in ev.py.modules:
            for site in module.calls:
                if site.qualname.startswith("torchvision.datasets."):
                    code_tokens.add(site.qualname.rsplit(".", 1)[-1].lower())
        for dataset in ev.manifest.datasets:
            code_tokens.add(dataset.id.lower())
        for config in ev.config.files:
            for key, value in config.values.items():
                dataset_key = "dataset" in key.lower() or key.lower().rsplit(".", 1)[-1] == "data"
                if dataset_key and isinstance(value, str):
                    code_tokens.add(value.lower())
        haystack = " ".join(code_tokens)

        def in_code(name: str) -> bool:
            compact = name.replace("-", "").replace(" ", "")
            return name in haystack or compact in haystack.replace("-", "").replace(" ", "")

        matched = {name for name in mentioned if in_code(name)}
        if matched:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message="Dataset(s) named in the paper appear in code/configs: " + ", ".join(sorted(matched)) + ".",
            )
        if not code_tokens:
            return self.finding(
                Status.UNKNOWN,
                confidence=0.5,
                message="The paper names dataset(s) (" + ", ".join(sorted(mentioned)) + ") but no code-side dataset "
                "identifiers were detected to compare against.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.55,
            message="Dataset(s) named in the paper (" + ", ".join(sorted(mentioned)) + ") do not match any "
            "dataset identifier found in code or configs.",
            remediation="Name the dataset consistently, and declare it in .adduce/manifest.yaml.",
        )


class HardwareClaimRule(Rule):
    id = "R-DRIFT-005"
    category = Category.DRIFT
    title = "Paper's hardware/runtime claims absent from the artifact"
    rationale = (
        "When the paper states hardware and runtime but the repository does not, the artifact "
        "cannot back the paper's compute claims."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        paper_claims = ev.latex.mentions_hardware or ev.latex.mentions_runtime
        if not paper_claims:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No hardware/runtime claims extracted from the paper.")
        artifact_side = (
            bool(ev.manifest.environment.hardware)
            or ev.docs.has_section("hardware")
            or ev.docs.mentions_hardware_inline
            or any(s.gpu_request for s in ev.runs.slurm_scripts)
        )
        if artifact_side:
            return self.finding(
                Status.PASS, confidence=0.7, message="The paper's hardware/runtime claims have artifact-side counterparts."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.65,
            message="The paper states hardware/runtime, but the repository documents neither.",
            remediation="Mirror the paper's hardware and runtime in the README hardware section and the manifest.",
        )


class AblationTraceRule(Rule):
    id = "R-DRIFT-006"
    category = Category.DRIFT
    title = "Ablations mentioned without matching configs or commands"
    rationale = (
        "Each ablation row is an experiment someone may try to rerun; a low-confidence hint "
        "when nothing in the repo obviously produces them."
    )
    weight = 1

    def applies_to(self, repo: Repo) -> bool:
        return any(f.suffix == ".tex" for f in repo.files)

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.latex.ablation_mentions:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No ablations mentioned in the paper.")
        artifacts = [f.path for f in ev.config.files if "ablat" in f.path.lower()]
        artifacts += [c.command for c in ev.runs.commands if "ablat" in c.command.lower()]
        artifacts += [str(f.path) for f in ev.repo.find("*ablat*")]
        if artifacts:
            return self.finding(
                Status.PASS,
                confidence=0.55,
                message=f"Ablations are mentioned and ablation artifacts exist (e.g. {str(artifacts[0])[:60]}).",
            )
        file, line = ev.latex.ablation_mentions[0]
        return self.finding(
            Status.PARTIAL,
            confidence=0.45,  # deliberately low: a hint, not a defect
            message="The paper mentions ablations, but no config, script, or command referencing them was found.",
            remediation="Commit the ablation configs/commands, or note in the README how the ablation rows were produced.",
            locations=[Location(file, line)],
        )
