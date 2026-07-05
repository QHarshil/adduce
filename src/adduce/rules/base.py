"""Rule and finding primitives.

Rules are pure: they read typed evidence and return a finding. They declare
an applicability predicate so a scikit-learn-only repository is never scored
against CUDA determinism flags, and every finding carries a status *and* a
confidence — static analysis detects signals, it does not certify outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..evidence import Evidence
from ..model import Repo


class Status(Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    NOT_APPLICABLE = "not-applicable"
    UNKNOWN = "unknown"

    @property
    def score_value(self) -> float | None:
        """Contribution toward the rule's weight; None means excluded from scoring."""
        return {"pass": 1.0, "partial": 0.5, "fail": 0.0}.get(self.value)


class Category(Enum):
    CODE_EXECUTION = "Code & Execution"
    ENVIRONMENT = "Environment & Tooling"
    DEPENDENCIES = "Dependencies"
    DATA = "Data"
    DOCUMENTATION = "Documentation"
    DETERMINISM = "Determinism & Model"
    PRECISION = "Numerical Precision & Hardware"
    DRIFT = "Paper & Artifact Consistency"
    RESULTS = "Result Reconciliation"
    RUN = "Run Traceability"
    CHECKPOINT = "Checkpoint & Experiment State"
    NOTEBOOK = "Notebooks"
    PORTABILITY = "Portability"
    REMOTE = "Remote Artifacts & Rot"
    VERSIONING = "Versioning"
    ACCESS_LEGAL = "Access & Legal"
    ARCHIVAL = "Archival Readiness"


@dataclass(frozen=True)
class Location:
    path: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class Finding:
    """The outcome of evaluating one rule against one repository."""

    rule_id: str
    category: Category
    title: str
    status: Status
    confidence: float
    message: str
    remediation: str
    weight: int
    locations: list[Location] = field(default_factory=list)
    fix_command: str | None = None
    suppressed: bool = False

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category.value,
            "title": self.title,
            "status": self.status.value,
            "confidence": self.confidence,
            "message": self.message,
            "remediation": self.remediation,
            "weight": self.weight,
            "locations": [{"path": loc.path, "line": loc.line} for loc in self.locations],
            "fix_command": self.fix_command,
            "suppressed": self.suppressed,
        }


class Rule:
    """Base class for all checks.

    Subclasses set the class attributes and implement :meth:`evaluate`.
    ``applies_to`` gates the rule on detected frameworks or repository shape;
    inapplicable rules are excluded from the score entirely rather than
    counted as passes.
    """

    id: str = ""
    category: Category
    title: str = ""
    rationale: str = ""
    weight: int = 1
    fix_command: str | None = None

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers for subclasses ---------------------------------------------

    def finding(
        self,
        status: Status,
        confidence: float,
        message: str,
        remediation: str = "",
        locations: list[Location] | None = None,
    ) -> Finding:
        return Finding(
            rule_id=self.id,
            category=self.category,
            title=self.title,
            status=status,
            confidence=confidence,
            message=message,
            remediation=remediation,
            weight=self.weight,
            locations=locations or [],
            fix_command=self.fix_command,
        )
