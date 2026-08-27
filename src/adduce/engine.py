"""The check pipeline: scan, collect evidence, evaluate rules, score."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, load_config
from .evidence import Evidence, collect
from .graph import ClaimGraph, build_graph
from .model import Repo, scan_repository
from .profiles import Profile, load_profile
from .reviewer_time import ReviewerTime
from .reviewer_time import estimate as estimate_reviewer_time
from .rules import BUILTIN_RULES, Category, Finding, Rule, Status, discover_rules
from .rules.registry import RulePluginWarning, safe_label
from .scoring import ScoreCard, score
from .telemetry import Telemetry


@dataclass
class CheckResult:
    repo: Repo
    evidence: Evidence
    card: ScoreCard
    config: Config
    graph: ClaimGraph
    reviewer_time: ReviewerTime
    telemetry: Telemetry = field(default_factory=Telemetry)


def _apply_suppressions(finding: Finding, evidence: Evidence, config: Config) -> None:
    """Mark a finding suppressed via config allowlist or inline pragma.

    An inline ``# adduce: ignore=R-XXX-000`` suppresses the finding when it
    sits on any of the finding's reported source lines; findings without
    line-level locations are suppressible only through configuration.
    """
    if finding.rule_id in config.ignore:
        finding.suppressed = True
        return
    for location in finding.locations:
        per_file = evidence.py.suppressions.get(location.path)
        if per_file and location.line is not None:
            ids = per_file.get(location.line, set())
            if finding.rule_id in ids:
                finding.suppressed = True
                return


#: The exact classes that ship with adduce. Membership is the test for
#: built-in-ness because it is the only one a rule pack cannot forge: any
#: predicate over ``__module__`` is defeated by one assignment in a class body,
#: and a rule that talks its way into the built-in branch gets the power to
#: abort the whole audit. A pack subclassing a built-in is not a member and so
#: stays contained, which is the safe direction to be wrong in.
_BUILTIN_RULE_CLASSES: tuple[type[Rule], ...] = tuple(BUILTIN_RULES)


def _is_third_party(rule: Rule) -> bool:
    # Identity, not set membership: ``in`` on a set consults ``__hash__`` and
    # ``__eq__``, and a rule pack supplies its own metaclass. A metaclass whose
    # ``__eq__`` returns True and whose ``__hash__`` matches a built-in's reads
    # as a built-in and regains the power to abort the audit, and one whose
    # ``__hash__`` raises aborts it from inside the test itself. ``is`` cannot
    # be overridden, so the comparison stays a question about this class rather
    # than one the class gets to answer.
    return not any(type(rule) is builtin for builtin in _BUILTIN_RULE_CLASSES)


def _type_label(value: object, fallback: str) -> str:
    """Name a value's type without handing a rule pack a free text channel.

    A class name is only an identifier when it was declared as one: built
    through ``type()`` it is arbitrary text of arbitrary length, and echoing it
    verbatim is enough to forge a heading and break a table row in a rendered
    report. A name that is not an identifier is dropped rather than repaired --
    a punctuation-mangled string tells a reader less than saying the name was
    unusable, and repairing it would still pass the characters through.

    The read itself is guarded. ``__name__`` on a class is served by its
    metaclass, and a rule pack supplies its own; ``getattr`` with a default
    suppresses only ``AttributeError``, so a metaclass property that raises
    anything else would escape. This function is what names a rule the run has
    already given up on, so it is the last place that may fail.
    """
    try:
        name = getattr(type(value), "__name__", None)
    except Exception:
        return fallback
    if not isinstance(name, str) or not name.isidentifier():
        return fallback
    return safe_label(name)


@dataclass(frozen=True)
class _RuleIdentity:
    """What a rule must be able to say about itself before a run can use it."""

    id: str
    category: Category
    title: str
    weight: int
    severity: str


def _identify(rule: Rule) -> _RuleIdentity | None:
    """Read what a result needs from a rule, or None when the rule cannot say.

    Each of these is an attribute lookup on an object a rule pack wrote, and a
    pack may define any of them as a property that raises. They are read once,
    here, and every later use is of this record rather than of the rule, so a
    rule cannot answer one way when the engine asks and another way when the
    finding it returned is checked against that answer. The finding is a
    separate object and is not pinned this way.

    A rule that cannot supply them is passed over rather than named. A finding
    needs an id, a category and a title before it can appear in a report, a
    score or a baseline, and synthesising them would file a result under a rule
    that does not exist.
    """
    try:
        return _RuleIdentity(
            id=rule.id,
            category=rule.category,
            title=rule.title,
            weight=rule.weight,
            severity=rule.effective_severity,
        )
    except Exception:
        if not _is_third_party(rule):
            raise
        return None


def _degrade(identity: _RuleIdentity, reason: str, telemetry: Telemetry) -> Finding:
    """Stand in for a third-party rule that produced no usable result.

    UNKNOWN, not a verdict: the rule applied and reached no assessment, so the
    run reports lower coverage rather than a score no rule earned. The rule's
    own identity is carried so the report still shows it was considered.

    ``reason`` reaches both a warning and a rendered report, so every fragment
    of it a rule pack controls arrives already through ``safe_label``.
    """
    warnings.warn(
        f"Recorded adduce rule {safe_label(identity.id)} as unknown: {reason}.",
        RulePluginWarning,
        stacklevel=2,
    )
    telemetry.count("rules.degraded")
    return Finding(
        rule_id=identity.id,
        category=identity.category,
        title=identity.title,
        status=Status.UNKNOWN,
        confidence=0.0,
        message=f"This check did not complete: {reason}. Its result is unknown.",
        remediation=(
            "Report the failure to whoever maintains this rule. A check that "
            "did not complete says nothing about this repository."
        ),
        weight=identity.weight,
        severity=identity.severity,
    )


def _evaluate_guarded(
    rule: Rule, evidence: Evidence, identity: _RuleIdentity, telemetry: Telemetry
) -> Finding:
    """Evaluate one rule, containing a third-party rule that misbehaves.

    One installed rule pack must not be able to discard an entire audit. A
    built-in doing either of these things is adduce's own bug and propagates:
    degrading it would bury the defect under a lowered coverage number, where
    nobody would look for it.
    """
    try:
        finding = rule.evaluate(evidence)
        # Read the identity inside the boundary too. ``isinstance`` consults
        # ``__class__``, which an object may define as a property, so passing
        # the type test does not mean the attribute reads will work; and a real
        # subclass may define ``rule_id`` as a property that raises. Either way
        # the failure belongs to the rule, not to the run.
        reported_id = finding.rule_id if isinstance(finding, Finding) else None
    except Exception as error:
        if not _is_third_party(rule):
            raise
        label = _type_label(error, "an exception with no usable class name")
        return _degrade(identity, f"the rule raised {label}", telemetry)
    if reported_id is None or reported_id != identity.id:
        # Both leave this rule unrepresented: a missing ``return`` yields no
        # finding at all, and a finding filed under another id takes that
        # rule's place in the report, the score and the baseline.
        if isinstance(finding, Finding):
            reason = "the rule reported under another rule's id"
        else:
            label = _type_label(finding, "a value with no usable class name")
            reason = f"the rule returned {label}, not a finding"
        if not _is_third_party(rule):
            raise ValueError(f"rule {identity.id!r} returned an unusable finding: {reason}")
        return _degrade(identity, reason, telemetry)
    telemetry.count("rules.evaluated")
    return finding


def run_check(
    path: Path,
    profile_name: str | None = None,
    ignore: frozenset[str] = frozenset(),
    exclude: tuple[str, ...] = (),
    include_plugins: bool = True,
    rules: list[Rule] | None = None,
    paper: Path | None = None,
    online: bool = False,
    honor_repository_policy: bool = True,
    honor_gitignore: bool = True,
) -> CheckResult:
    """Run the full pipeline against a repository root.

    ``paper`` points at LaTeX sources kept outside the repository (a common
    layout: paper and code in separate repos). It may be a directory or a
    ``.tex`` file; its extraction replaces whatever the repository itself
    contains, and evidence locations are relative to the paper root.
    """
    repository_config = load_config(path)
    if honor_repository_policy:
        config = repository_config
    else:
        config = Config(
            source=repository_config.source,
            repository_policy_honored=False,
        )
    if profile_name:
        config.profile = profile_name
    if ignore:
        config.ignore = config.ignore | ignore
    if exclude:
        config.exclude = tuple(dict.fromkeys([*config.exclude, *exclude]))

    telemetry = Telemetry()
    with telemetry.stage("total"):
        profile: Profile = load_profile(config.profile, allow_path=profile_name is not None)
        with telemetry.stage("scan"):
            repo = scan_repository(
                path,
                exclude=config.exclude,
                honor_gitignore=honor_gitignore,
            )
        evidence = collect(repo, telemetry=telemetry)
        if evidence.manifest.error:
            raise ValueError(evidence.manifest.error)
        if online:
            from .cache import Cache
            from .dynamic.resolve import resolve_references

            evidence.remote.online_attempted = True
            with telemetry.stage("resolve.online"):
                evidence.remote.resolutions = resolve_references(
                    evidence.remote.references,
                    Cache(repo.root),
                )
        if paper is not None:
            from .evidence.latex import collect_latex

            paper_root = paper if paper.is_dir() else paper.parent
            with telemetry.stage("collect.latex.paper"):
                evidence.latex = collect_latex(
                    scan_repository(paper_root, honor_gitignore=honor_gitignore)
                )

        with telemetry.stage("rules.discover"):
            discovered = (
                rules if rules is not None else discover_rules(include_plugins=include_plugins)
            )

        findings: list[Finding] = []
        skipped_inapplicable = 0
        with telemetry.stage("rules.evaluate"):
            for rule in discovered:
                identity = _identify(rule)
                if identity is None:
                    telemetry.count("rules.skipped_unidentifiable")
                    warnings.warn(
                        "Passed over an adduce rule from "
                        f"{_type_label(rule, 'a class with no usable name')}: it could "
                        "not supply the identity a result is filed under.",
                        RulePluginWarning,
                        stacklevel=2,
                    )
                    continue
                if identity.id in profile.disabled_rules:
                    telemetry.count("rules.skipped_disabled")
                    continue
                try:
                    applies = rule.applies_to(repo)
                except Exception as error:
                    if not _is_third_party(rule):
                        raise
                    # Not the same as answering "no". The run does not know
                    # whether this rule applied, and a rule that answered "no"
                    # leaves the score untouched, so recording it as
                    # inapplicable would claim something nothing established.
                    label = _type_label(error, "an exception with no usable class name")
                    finding = _degrade(
                        identity,
                        f"the rule's applicability check raised {label}",
                        telemetry,
                    )
                else:
                    if not applies:
                        skipped_inapplicable += 1
                        telemetry.count("rules.skipped_inapplicable")
                        continue
                    finding = _evaluate_guarded(rule, evidence, identity, telemetry)
                _apply_suppressions(finding, evidence, config)
                findings.append(finding)

        with telemetry.stage("score"):
            card = score(
                findings,
                profile,
                analysable_lines=sum(module.line_count for module in evidence.py.modules),
                skipped_inapplicable=skipped_inapplicable,
            )
        with telemetry.stage("graph"):
            graph = build_graph(evidence)
        with telemetry.stage("reviewer_time"):
            reviewer_time = estimate_reviewer_time(evidence)

    _record_counters(telemetry, repo, evidence)
    return CheckResult(
        repo=repo,
        evidence=evidence,
        card=card,
        config=config,
        graph=graph,
        reviewer_time=reviewer_time,
        telemetry=telemetry,
    )


def _record_counters(telemetry: Telemetry, repo: Repo, evidence: Evidence) -> None:
    """Record the work the run actually did, after it has finished doing it.

    Everything here is derived from state the pipeline already holds, so
    counting costs nothing on the hot path.
    """
    telemetry.count("files.inventoried", len(repo.files))
    telemetry.count("files.python", len(repo.python_files()))
    cache_hits, disk_reads = repo.read_cache_stats()
    telemetry.count("files.read_cache_hits", cache_hits)
    telemetry.count("files.read_from_disk", disk_reads)
    telemetry.count("parse.python.modules", len(evidence.py.modules))
    telemetry.count(
        "parse.python.failed",
        sum(1 for module in evidence.py.modules if module.parse_error),
    )


# -- baseline / ratchet -----------------------------------------------------

BASELINE_FILENAME = ".adduce/baseline.json"

_STATUS_ORDER = {
    Status.PASS: 3,
    Status.PARTIAL: 2,
    Status.FAIL: 1,
}
_BASELINE_STATUSES = frozenset(status.value for status in _STATUS_ORDER)


def baseline_snapshot(card: ScoreCard) -> dict:
    return {
        "version": 1,
        "total": round(card.total, 1) if card.total is not None else None,
        "profile": card.profile_name,
        "rules": {
            f.rule_id: f.status.value
            for f in card.findings
            if f.status.is_assessed
        },
    }


def regressions_against(card: ScoreCard, baseline: dict[str, object]) -> list[Finding]:
    """Findings that are strictly worse than their recorded baseline status.

    Rules absent from the baseline (new rules, newly applicable) are not
    regressions: adoption must never punish upgrading the tool.
    """
    if baseline.get("version") != 1:
        raise ValueError("invalid baseline: expected version 1")
    recorded_value = baseline.get("rules")
    if not isinstance(recorded_value, dict) or any(
        not isinstance(rule_id, str)
        or not isinstance(status, str)
        or status not in _BASELINE_STATUSES
        for rule_id, status in recorded_value.items()
    ):
        raise ValueError("invalid baseline: 'rules' must map rule IDs to statuses")
    recorded = {
        rule_id: status
        for rule_id, status in recorded_value.items()
        if isinstance(rule_id, str) and isinstance(status, str)
    }
    regressed: list[Finding] = []
    for finding in card.findings:
        if finding.suppressed or not finding.status.is_assessed:
            continue
        previous = recorded.get(finding.rule_id)
        if previous is None:
            continue
        try:
            previous_status = Status(previous)
        except ValueError:
            continue
        if _STATUS_ORDER.get(finding.status, 0) < _STATUS_ORDER.get(previous_status, 0):
            regressed.append(finding)
    return regressed
