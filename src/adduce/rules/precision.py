"""Numerical Precision & Hardware.

TF32 is not the problem; *undocumented* TF32 is. Every rule here is a
warning/partial at worst — precision choices are legitimate, they just have
to be written down, because they are a leading cause of cross-hardware
result differences.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status


def _precision_documented(ev: Evidence) -> bool:
    return bool(ev.manifest.environment.precision) or ev.latex.mentions_precision or _readme_mentions_precision(ev)


def _readme_mentions_precision(ev: Evidence) -> bool:
    # Cheap proxy via docs evidence: hardware section plus a precision keyword
    # is collected by the LaTeX regex only for papers, so check README headings.
    return any(
        "precision" in heading.lower() or "fp16" in heading.lower() or "bf16" in heading.lower()
        for heading in ev.docs.headings
    )


def _hardware_documented(ev: Evidence) -> bool:
    return (
        bool(ev.manifest.environment.hardware)
        or ev.docs.has_section("hardware")
        or ev.docs.mentions_hardware_inline
        or ev.latex.mentions_hardware
    )


class _PrecisionRule(Rule):
    """Shared shape: detected events + a documentation predicate → partial/pass."""

    events_kinds: tuple[str, ...] = ()
    subject: str = ""

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses_any({"torch", "lightning", "transformers"})

    def evaluate(self, ev: Evidence) -> Finding:
        events = ev.precision.of_kind(*self.events_kinds)
        if not events:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message=f"No {self.subject} detected.")
        documented = _precision_documented(ev)
        locations = [Location(e.file, e.line) for e in events[:5]]
        details = "; ".join(dict.fromkeys(e.detail for e in events[:4]))
        if documented:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message=f"{self.subject} in use ({details}) and a precision policy is documented.",
                locations=locations,
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.75,
            message=f"{self.subject} in use ({details}) but no precision policy is documented anywhere "
            "(manifest, README, or paper).",
            remediation=(
                "Document the precision policy: add a Hardware & Precision note to the README "
                "(`adduce fix --scaffold readme`) and set environment.precision in the manifest."
            ),
            locations=locations,
        )


class TF32Rule(_PrecisionRule):
    id = "R-PREC-001"
    category = Category.PRECISION
    title = "TF32 matmul enabled but undocumented"
    rationale = (
        "TF32 truncates float32 matmul mantissas on Ampere+ GPUs; results differ from true "
        "fp32 and from older hardware unless the setting is stated."
    )
    weight = 3
    events_kinds = ("tf32", "matmul_precision")
    subject = "TF32 / float32-matmul precision control"


class AmpRule(_PrecisionRule):
    id = "R-PREC-002"
    category = Category.PRECISION
    title = "Mixed precision (AMP/autocast) undocumented"
    rationale = "AMP changes numerics run-to-run and across GPU generations; the policy must be stated."
    weight = 3
    events_kinds = ("autocast", "grad_scaler")
    subject = "automatic mixed precision (autocast/GradScaler)"


class LowPrecisionCastRule(_PrecisionRule):
    id = "R-PREC-003"
    category = Category.PRECISION
    title = "FP16/BF16 computation without documented hardware"
    rationale = (
        "BF16 exists only on specific architectures and FP16 behaviour differs across them; "
        "low-precision results are not interpretable without the GPU/TPU stated."
    )
    weight = 3
    events_kinds = ("cast", "trainer_flag", "deepspeed")
    subject = "explicit low-precision computation (fp16/bf16 casts or trainer flags)"

    def evaluate(self, ev: Evidence) -> Finding:
        finding = super().evaluate(ev)
        # This rule cares specifically about hardware, not just a precision note.
        if finding.status is Status.PASS and not _hardware_documented(ev):
            events = ev.precision.of_kind(*self.events_kinds)
            return self.finding(
                Status.PARTIAL,
                confidence=0.7,
                message="Low-precision computation is documented, but the GPU/TPU architecture it ran on is not.",
                remediation="State the accelerator (e.g. 'A100 80GB, bf16') in the README hardware section and the manifest.",
                locations=[Location(e.file, e.line) for e in events[:5]],
            )
        return finding


class MatmulPrecisionRule(Rule):
    id = "R-PREC-004"
    category = Category.PRECISION
    title = "set_float32_matmul_precision used but undocumented"
    rationale = "A one-line global precision switch that silently changes every matmul in the program."
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        events = ev.precision.of_kind("matmul_precision")
        if not events:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="torch.set_float32_matmul_precision not detected.")
        if _precision_documented(ev):
            return self.finding(
                Status.PASS, confidence=0.7, message=f"{events[0].detail} is used and the precision policy is documented."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.75,
            message=f"{events[0].detail} is called but no precision policy is documented.",
            remediation="Document the matmul precision setting alongside the hardware note.",
            locations=[Location(e.file, e.line) for e in events[:3]],
        )


class GpuHardwareBaselineRule(Rule):
    id = "R-PREC-005"
    category = Category.PRECISION
    title = "GPU code without documented hardware"
    rationale = (
        "Even in plain fp32, results differ across GPU architectures (reduction orders, "
        "denormals). Any GPU-using artifact should state what it ran on."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses_any({"torch", "tensorflow", "jax", "lightning"})

    def evaluate(self, ev: Evidence) -> Finding:
        gpu_signals = (
            ev.py.calls_any("torch.cuda.manual_seed_all", "torch.cuda.manual_seed", "torch.cuda.is_available")
            or any(s.qualname.startswith("torch.cuda.") for m in ev.py.modules for s in m.calls)
            or bool(ev.precision.events)
            or any(s.gpu_request for s in ev.runs.slurm_scripts)
        )
        if not gpu_signals:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No clear GPU usage detected.")
        if _hardware_documented(ev):
            return self.finding(Status.PASS, confidence=0.75, message="GPU usage detected and the hardware is documented.")
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message="The code clearly targets GPUs, but no hardware is documented in the README, manifest, or paper.",
            remediation="Add a hardware note (GPU model, count, memory) — results are architecture-dependent even at fp32.",
        )
