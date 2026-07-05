"""The check pipeline: scan, collect evidence, evaluate rules, score."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config
from .evidence import Evidence, collect
from .graph import ClaimGraph, build_graph
from .model import Repo, scan_repository
from .profiles import Profile, load_profile
from .reviewer_time import ReviewerTime
from .reviewer_time import estimate as estimate_reviewer_time
from .rules import Finding, Rule, Status, discover_rules
from .scoring import ScoreCard, score


@dataclass
class CheckResult:
    repo: Repo
    evidence: Evidence
    card: ScoreCard
    config: Config
    graph: ClaimGraph
    reviewer_time: ReviewerTime


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


def run_check(
    path: Path,
    profile_name: str | None = None,
    ignore: frozenset[str] = frozenset(),
    exclude: tuple[str, ...] = (),
    include_plugins: bool = True,
    rules: list[Rule] | None = None,
    paper: Path | None = None,
) -> CheckResult:
    """Run the full pipeline against a repository root.

    ``paper`` points at LaTeX sources kept outside the repository (a common
    layout: paper and code in separate repos). It may be a directory or a
    ``.tex`` file; its extraction replaces whatever the repository itself
    contains, and evidence locations are relative to the paper root.
    """
    config = load_config(path)
    if profile_name:
        config.profile = profile_name
    if ignore:
        config.ignore = config.ignore | ignore
    if exclude:
        config.exclude = tuple(dict.fromkeys([*config.exclude, *exclude]))

    profile: Profile = load_profile(config.profile)
    repo = scan_repository(path, exclude=config.exclude)
    evidence = collect(repo)
    if paper is not None:
        from .evidence.latex import collect_latex

        paper_root = paper if paper.is_dir() else paper.parent
        evidence.latex = collect_latex(scan_repository(paper_root))

    findings: list[Finding] = []
    for rule in rules if rules is not None else discover_rules(include_plugins=include_plugins):
        if rule.id in profile.disabled_rules:
            continue
        if not rule.applies_to(repo):
            continue
        finding = rule.evaluate(evidence)
        _apply_suppressions(finding, evidence, config)
        findings.append(finding)

    card = score(findings, profile)
    return CheckResult(
        repo=repo,
        evidence=evidence,
        card=card,
        config=config,
        graph=build_graph(evidence),
        reviewer_time=estimate_reviewer_time(evidence),
    )


# -- baseline / ratchet -----------------------------------------------------

BASELINE_FILENAME = ".adduce/baseline.json"

_STATUS_ORDER = {
    Status.PASS: 3,
    Status.PARTIAL: 2,
    Status.FAIL: 1,
}


def baseline_snapshot(card: ScoreCard) -> dict:
    return {
        "version": 1,
        "total": round(card.total, 1),
        "profile": card.profile_name,
        "rules": {
            f.rule_id: f.status.value
            for f in card.findings
            if f.status.score_value is not None
        },
    }


def regressions_against(card: ScoreCard, baseline: dict) -> list[Finding]:
    """Findings that are strictly worse than their recorded baseline status.

    Rules absent from the baseline (new rules, newly applicable) are not
    regressions: adoption must never punish upgrading the tool.
    """
    recorded: dict[str, str] = baseline.get("rules", {})
    regressed: list[Finding] = []
    for finding in card.findings:
        if finding.suppressed or finding.status.score_value is None:
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
