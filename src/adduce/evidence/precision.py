"""Numerical-precision evidence: TF32, AMP, and low-precision casts.

None of these are problems in themselves — undocumented, they are a leading
cause of cross-hardware result differences. This collector only records what
the code does; the R-PREC rules compare it against what the docs and paper
say, and always report warnings, never failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ConfigEvidence
from .python_ast import PythonEvidence


@dataclass(frozen=True)
class PrecisionEvent:
    kind: str    # tf32 | matmul_precision | autocast | grad_scaler | cast | trainer_flag | deepspeed
    detail: str
    file: str
    line: int


@dataclass
class PrecisionEvidence:
    events: list[PrecisionEvent] = field(default_factory=list)

    def of_kind(self, *kinds: str) -> list[PrecisionEvent]:
        return [e for e in self.events if e.kind in kinds]

    @property
    def uses_tf32(self) -> bool:
        return bool(self.of_kind("tf32", "matmul_precision"))

    @property
    def uses_amp(self) -> bool:
        return bool(self.of_kind("autocast", "grad_scaler"))

    @property
    def uses_low_precision(self) -> bool:
        return bool(self.of_kind("cast", "trainer_flag", "deepspeed"))

    @property
    def any_precision_control(self) -> bool:
        return bool(self.events)


_LOW_PRECISION_TOKENS = ("float16", "bfloat16", "half", "fp16", "bf16")


def collect_precision(py: PythonEvidence, config: ConfigEvidence) -> PrecisionEvidence:
    evidence = PrecisionEvidence()

    # TF32 flags.
    for target in ("torch.backends.cuda.matmul.allow_tf32", "torch.backends.cudnn.allow_tf32"):
        for assign in py.assign_sites(target):
            if assign.value is True:
                evidence.events.append(
                    PrecisionEvent("tf32", f"{target} = True", assign.file, assign.line)
                )
    for site in py.call_sites("torch.set_float32_matmul_precision"):
        detail = "torch.set_float32_matmul_precision(...)"
        if site.first_arg:
            detail = f'torch.set_float32_matmul_precision("{site.first_arg}")'
        evidence.events.append(PrecisionEvent("matmul_precision", detail, site.file, site.line))

    # Autocast / GradScaler.
    for name in ("torch.autocast", "torch.cuda.amp.autocast", "torch.amp.autocast"):
        for site in py.call_sites(name):
            dtype = site.kw_value("dtype") or ""
            evidence.events.append(
                PrecisionEvent("autocast", f"{name}({dtype})".rstrip("()") or name, site.file, site.line)
            )
    for site in py.call_sites_terminal("GradScaler"):
        evidence.events.append(PrecisionEvent("grad_scaler", site.qualname, site.file, site.line))

    # Explicit casts, gated on torch being present to avoid unrelated .half().
    if "torch" in py.imports:
        for terminal in ("half", "bfloat16"):
            for site in py.call_sites_terminal(terminal):
                evidence.events.append(
                    PrecisionEvent("cast", f"{site.qualname}()", site.file, site.line)
                )
    # dtype= / torch_dtype= keyword values naming a low-precision type.
    for module in py.modules:
        for site in module.calls:
            for key in ("dtype", "torch_dtype"):
                value = site.kw_value(key)
                if value and any(token in value.lower() for token in _LOW_PRECISION_TOKENS):
                    evidence.events.append(
                        PrecisionEvent("cast", f"{key}={value}", site.file, site.line)
                    )

    # Framework flags: Lightning Trainer(precision=...), HF TrainingArguments(fp16/bf16/tf32).
    for site in py.call_sites_terminal("Trainer"):
        value = site.kw_value("precision")
        if value is not None:
            evidence.events.append(
                PrecisionEvent("trainer_flag", f"Trainer(precision={value})", site.file, site.line)
            )
    for site in py.call_sites_terminal("TrainingArguments"):
        for key in ("fp16", "bf16", "tf32"):
            value = site.kw_value(key)
            if value is not None and value != "False":
                evidence.events.append(
                    PrecisionEvent("trainer_flag", f"TrainingArguments({key}={value})", site.file, site.line)
                )

    # DeepSpeed JSON configs.
    for cfg in config.files:
        if not cfg.is_deepspeed:
            continue
        for key in ("fp16.enabled", "bf16.enabled"):
            if cfg.values.get(key) is True:
                evidence.events.append(PrecisionEvent("deepspeed", f"{cfg.path}: {key}=true", cfg.path, 1))

    return evidence
