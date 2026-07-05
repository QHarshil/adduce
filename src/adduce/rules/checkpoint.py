"""Checkpoint completeness: can training resume identically, and can a
checkpoint be traced back to what produced it?

Detected from the dict shape passed to ``torch.save``. Heuristic by nature —
the dict may be assembled beyond one assignment hop — so confidence stays
moderate and unknown shapes are not punished.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status

_OPTIMIZER_KEYS = frozenset({"optimizer", "optimizer_state", "optimizer_state_dict", "optim", "opt_state"})
_SCHEDULER_KEYS = frozenset({"scheduler", "lr_scheduler", "scheduler_state_dict", "lr_scheduler_state_dict", "sched"})
_PROGRESS_KEYS = frozenset({"epoch", "step", "global_step", "iteration", "iter", "batch_idx"})
_RNG_KEYS = frozenset({"rng_state", "rng", "torch_rng_state", "numpy_rng_state", "random_state", "cuda_rng_state", "rng_states"})
_PROVENANCE_KEYS = frozenset({"config", "cfg", "args", "hparams", "hyper_parameters", "commit", "git_commit", "git_sha", "versions", "library_versions", "data_hash", "wandb_id"})


def _uses_framework_checkpointing(ev: Evidence) -> bool:
    """Lightning/HF trainers save complete training state themselves."""
    return (
        ev.repo.frameworks.uses("lightning")
        or bool(ev.py.call_sites_terminal("ModelCheckpoint"))
        or any("save_strategy" in s.keywords or "save_steps" in s.keywords for s in ev.py.call_sites_terminal("TrainingArguments"))
    )


class _CheckpointBase(Rule):
    keys: frozenset[str] = frozenset()
    what: str = ""
    add_remediation: str = ""

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        saves = ev.py.torch_saves
        if not saves:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No torch.save call detected.")
        if _uses_framework_checkpointing(ev):
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message="Framework checkpointing (Lightning/HF Trainer) manages full training state.",
            )
        visible = [s for s in saves if s.dict_keys is not None]
        if not visible:
            return self.finding(
                Status.UNKNOWN,
                confidence=0.4,
                message="torch.save payload shapes could not be resolved statically; cannot judge checkpoint completeness.",
            )
        weights_only = [s for s in visible if not s.saves_dict]
        with_key = [s for s in visible if s.dict_keys and {k.lower() for k in s.dict_keys} & self.keys]
        if with_key:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message=f"Checkpoint dict includes {self.what} at {len(with_key)} save site(s) (heuristic).",
            )
        gaps = weights_only + [s for s in visible if s.saves_dict and not ({k.lower() for k in (s.dict_keys or ())} & self.keys)]
        return self.finding(
            Status.PARTIAL,
            confidence=0.55,
            message=f"No torch.save site visibly includes {self.what}"
            + (" (some sites save bare state_dicts)" if weights_only else "") + ".",
            remediation=self.add_remediation,
            locations=[Location(s.file, s.line) for s in gaps[:4]],
        )


class OptimizerStateRule(_CheckpointBase):
    id = "R-CKPT-001"
    category = Category.CHECKPOINT
    title = "Checkpoints include optimizer state"
    rationale = "Weights-only checkpoints cannot resume training identically; Adam moments restart from zero."
    weight = 3
    keys = _OPTIMIZER_KEYS
    what = "optimizer state"
    add_remediation = "Save {'model': model.state_dict(), 'optimizer': optimizer.state_dict(), ...} rather than the bare state_dict."


class SchedulerStateRule(_CheckpointBase):
    id = "R-CKPT-002"
    category = Category.CHECKPOINT
    title = "Checkpoints include scheduler state"
    rationale = "A resumed run with a reset LR schedule follows a different training trajectory."
    weight = 2
    keys = _SCHEDULER_KEYS
    what = "LR-scheduler state"
    add_remediation = "Add 'scheduler': scheduler.state_dict() to the checkpoint dict."


class ProgressStateRule(_CheckpointBase):
    id = "R-CKPT-003"
    category = Category.CHECKPOINT
    title = "Checkpoints record epoch/step"
    rationale = "Without the training position, resuming re-runs or skips data and breaks comparisons."
    weight = 2
    keys = _PROGRESS_KEYS
    what = "epoch/step progress"
    add_remediation = "Record 'epoch' and 'global_step' in the checkpoint dict."


class RngStateRule(_CheckpointBase):
    id = "R-CKPT-004"
    category = Category.CHECKPOINT
    title = "Checkpoints capture RNG state"
    rationale = "Identical resumption needs the RNG states (torch, cuda, numpy, random) saved and restored."
    weight = 2
    keys = _RNG_KEYS
    what = "RNG state"
    add_remediation = "Save torch.get_rng_state(), torch.cuda.get_rng_state_all(), and numpy/random states in the checkpoint."


class ProvenanceRule(_CheckpointBase):
    id = "R-CKPT-005"
    category = Category.CHECKPOINT
    title = "Checkpoints record config/commit provenance"
    rationale = (
        "A checkpoint that records its config, commit, and library versions can be traced "
        "back to what produced it months later; one that does not is an orphan."
    )
    weight = 2
    keys = _PROVENANCE_KEYS
    what = "config/commit/version provenance"
    add_remediation = "Store the config, git commit, and library versions inside the checkpoint dict."
