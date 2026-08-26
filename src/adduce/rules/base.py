"""Rule and finding primitives.

Rules are pure: they read typed evidence and return a finding. They declare
an applicability predicate so a scikit-learn-only repository is never scored
against CUDA determinism flags, and every finding carries a status *and* a
confidence — static analysis detects signals, it does not certify outcomes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from types import MappingProxyType

from ..evidence import Evidence
from ..model import Repo

#: What a finding item's ``attributes`` may hold: JSON scalars only. Integrations
#: read these instead of parsing prose, so nested containers and binary blobs are
#: refused at construction. Widening this stays backward compatible; narrowing it
#: would not.
JsonValue = str | int | float | bool | None


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

    @property
    def is_applicable(self) -> bool:
        """Whether the check applies to this repository at all.

        Membership over the members, not a test of ``score_value``: UNKNOWN and
        NOT_APPLICABLE both carry no value and differ only here.
        """
        return self in _APPLICABLE

    @property
    def is_assessed(self) -> bool:
        """Whether the check applies and reached an answer.

        Membership for the same reason ``is_applicable`` is: a missing value
        says nothing about which of the two unvalued states this is.
        """
        return self in _ASSESSED


_APPLICABLE = frozenset({Status.PASS, Status.PARTIAL, Status.FAIL, Status.UNKNOWN})
_ASSESSED = frozenset({Status.PASS, Status.PARTIAL, Status.FAIL})


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


def _validate_confidence(value: float, label: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} is not a number, got {type(value).__name__}")
    if not isfinite(value):
        raise ValueError(f"{label} is not finite, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{label} is outside 0.0..1.0, got {value!r}")


def _validate_attributes(attributes: Mapping[str, JsonValue], label: str) -> None:
    """Require attribute values that JSON can represent exactly.

    Checked at construction rather than at write time so the offending producer
    is named, not the serializer.
    """
    for key, value in attributes.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} attribute key {key!r} is not a string")
        if value is None or isinstance(value, (str, bool, int)):
            continue
        if isinstance(value, float):
            if not isfinite(value):
                raise ValueError(f"{label} attribute {key!r} is not finite, got {value!r}")
            continue
        raise ValueError(
            f"{label} attribute {key!r} holds an unrepresentable {type(value).__name__}"
        )


@dataclass(frozen=True)
class FindingItem:
    """One structured observation explaining a parent finding's verdict.

    Non-recursive by construction: an item carries no items. ``id`` is unique
    within the parent and stable under reordering, so prefer a domain identity
    over a position. ``kind`` is an open string because external rule packs need
    domain-specific values.

    An item is explanatory. It is never independently scored, baselined or
    suppressed — that is what keeps a rule checking thousands of assertions
    from outweighing its neighbours.
    """

    id: str
    status: Status
    message: str
    confidence: float = 1.0
    locations: tuple[Location, ...] = ()
    remediation: str = ""
    kind: str | None = None
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            raise ValueError(f"finding item id is not a string, got {type(self.id).__name__}")
        if not self.id:
            raise ValueError("finding item id is empty")
        label = f"finding item {self.id!r}"
        if not isinstance(self.message, str):
            raise ValueError(f"{label} message is not a string, got {type(self.message).__name__}")
        _validate_confidence(self.confidence, f"{label} confidence")
        _validate_attributes(self.attributes, label)
        # Defensive copies: a caller-owned dict or list mutated after construction
        # must never change an already-validated, frozen item.
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
        object.__setattr__(self, "locations", tuple(self.locations))

    def __reduce__(self) -> tuple:
        # The read-only attributes view cannot be copied by the default
        # protocol, which would otherwise cost this public type copy support it
        # had before. Rebuilding through __init__ re-validates and re-wraps.
        return (
            self.__class__,
            (
                self.id,
                self.status,
                self.message,
                self.confidence,
                self.locations,
                self.remediation,
                self.kind,
                dict(self.attributes),
            ),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message,
            "confidence": self.confidence,
            "locations": [{"path": loc.path, "line": loc.line} for loc in self.locations],
            "remediation": self.remediation,
            "kind": self.kind,
            "attributes": dict(self.attributes),
        }


def summarize_items(items: Iterable[FindingItem]) -> dict[Status, int]:
    """Per-status counts over ``items``, every :class:`Status` member present.

    Zeros are materialised rather than created on first increment, so a consumer
    can index any member without a presence check. No policy is imposed: the
    rule author chooses the parent status from the rule's own documented
    semantics, and this only reports what the children say.
    """
    counts = dict.fromkeys(Status, 0)
    for item in items:
        counts[item.status] += 1
    return counts


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
    severity: str = "medium"
    locations: list[Location] = field(default_factory=list)
    fix_command: str | None = None
    suppressed: bool = False
    #: Observations explaining this verdict. The finding stays the unit of rule
    #: identity, scoring, category weight, baseline tracking and suppression.
    items: tuple[FindingItem, ...] = ()

    def __post_init__(self) -> None:
        # Materialise before validating: an iterator would be consumed by the
        # duplicate check and leave nothing to serialise, and a caller's list
        # would stay aliased and admit ids this check never saw.
        self.items = tuple(self.items)
        seen: set[str] = set()
        for item in self.items:
            if item.id in seen:
                raise ValueError(f"duplicate finding item id {item.id!r} on {self.rule_id!r}")
            seen.add(item.id)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category.value,
            "title": self.title,
            "status": self.status.value,
            "confidence": self.confidence,
            "severity": self.severity,
            "message": self.message,
            "remediation": self.remediation,
            "weight": self.weight,
            "locations": [{"path": loc.path, "line": loc.line} for loc in self.locations],
            "fix_command": self.fix_command,
            "suppressed": self.suppressed,
            # Always present, empty when there are none: a conditional key would
            # force every consumer to write a presence check.
            "items": [item.to_dict() for item in self.items],
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
    #: How much a violation matters, independent of how much this profile's
    #: score penalises it. A low-confidence high-severity finding must not
    #: read the same as a high-confidence low-severity one. When unset, a
    #: default is derived from the weight; rules override it where the two
    #: diverge (a committed secret is high severity at modest weight).
    severity: str | None = None  # "low" | "medium" | "high"
    fix_command: str | None = None

    @property
    def effective_severity(self) -> str:
        if self.severity is not None:
            return self.severity
        if self.weight >= 5:
            return "high"
        if self.weight >= 3:
            return "medium"
        return "low"

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
        *,
        items: Sequence[FindingItem] = (),
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
            severity=self.effective_severity,
            locations=locations or [],
            fix_command=self.fix_command,
            items=tuple(items),
        )
