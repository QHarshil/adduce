"""The claim-to-artifact graph.

For every major claim, assemble the trail an artifact reviewer would build
by hand: metric → producing script/command → config → data → environment →
seeds → commit, with a status per edge. Author-confirmed manifest edges are
authoritative; scaffolded draft claims remain inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .evidence import Evidence
from .manifest import Claim
from .manifest_builder import scaffold_manifest
from .rules.drift import values_match


class TrailStatus(Enum):
    SUPPORTED = "supported"  # every statically checkable recorded edge resolves
    PARTIAL = "partial"      # some edges resolve, some missing
    UNLINKED = "unlinked"    # claim exists but nothing ties it to artifacts


@dataclass
class TrailEntry:
    label: str           # "metric", "produced by", "config", ...
    value: str           # what the trail points at
    note: str = ""       # e.g. "~ rounding vs paper (0.814)"
    resolved: bool | None = None  # None = cannot check statically


@dataclass
class ClaimTrail:
    claim: Claim
    entries: list[TrailEntry] = field(default_factory=list)
    status: TrailStatus = TrailStatus.UNLINKED
    inferred: bool = False  # True when the claim was drafted, not authored

    @property
    def headline(self) -> str:
        where = f"{self.claim.where}  ·  " if self.claim.where else ""
        text = self.claim.text or self.claim.metric or self.claim.id
        return f'{where}"{text[:90]}"'


@dataclass
class ClaimGraph:
    trails: list[ClaimTrail] = field(default_factory=list)
    from_manifest: bool = False


def _check_metric(
    claim: Claim,
    ev: Evidence,
    entries: list[TrailEntry],
    use_declared_log: bool,
) -> bool | None:
    if not claim.metric or claim.value is None:
        return None
    log_path = claim.produced_by.log if use_declared_log else None
    matches = ev.results.lookup_metric(claim.metric, path=log_path)
    if not matches:
        entries.append(
            TrailEntry(
                label="metric",
                value=f"{claim.metric} = {claim.value:g}",
                note="no logged counterpart found (logs may be gitignored)",
                resolved=False,
            )
        )
        return False
    stated = claim.value
    candidates = [(source, value) for source, values in matches for value in values]
    source, closest = min(candidates, key=lambda item: abs(item[1] - stated))
    if values_match(stated, closest):
        note = "" if closest == stated else f"~ rounding vs paper ({stated:g})"
        entries.append(TrailEntry("metric", f"{source}  (found: {closest:g})", note, resolved=True))
        return True
    entries.append(
        TrailEntry(
            "metric",
            f"{source}  (closest: {closest:g})",
            f"differs from stated {stated:g}",
            resolved=False,
        )
    )
    return False


def _check_path(ev: Evidence, label: str, path: str | None, entries: list[TrailEntry]) -> bool | None:
    if not path:
        return None
    exists = ev.repo.exists(path)
    entries.append(
        TrailEntry(label, path, "" if exists else "path not found in the repository", resolved=exists)
    )
    return exists


def build_claim_trail(claim: Claim, ev: Evidence, inferred: bool) -> ClaimTrail:
    trail = ClaimTrail(claim=claim, inferred=inferred)
    entries = trail.entries

    outcomes: list[bool] = []

    metric_ok = _check_metric(claim, ev, entries, use_declared_log=not inferred)
    if metric_ok is not None:
        outcomes.append(metric_ok)

    if claim.produced_by.command:
        entries.append(TrailEntry("command", claim.produced_by.command, resolved=None))
    for label, path in (
        ("config", claim.produced_by.config),
        ("data", claim.produced_by.data),
        ("log", claim.produced_by.log),
    ):
        resolved = _check_path(ev, label, path, entries)
        if resolved is not None:
            outcomes.append(resolved)

    env_bits = []
    if ev.deps.lockfiles:
        env_bits.append(ev.deps.lockfiles[0])
    if ev.env.dockerfiles:
        env_bits.append(ev.env.dockerfiles[0])
    if env_bits:
        entries.append(TrailEntry("env", " + ".join(env_bits), resolved=True))

    if claim.seeds:
        entries.append(TrailEntry("seeds", ", ".join(str(s) for s in claim.seeds), resolved=True))
    if claim.produced_by.commit:
        head = (ev.repo.git.head_commit or "")[: len(claim.produced_by.commit)]
        at_head = head == claim.produced_by.commit
        entries.append(
            TrailEntry(
                "commit",
                claim.produced_by.commit,
                "" if at_head else "differs from current HEAD",
                resolved=None,
            )
        )

    checkable = list(outcomes)
    linked = bool(claim.produced_by.command or claim.produced_by.config or claim.produced_by.log)
    if not linked and not checkable:
        trail.status = TrailStatus.UNLINKED
    elif checkable and all(checkable) and linked:
        trail.status = TrailStatus.SUPPORTED
    else:
        trail.status = TrailStatus.PARTIAL
    return trail


def build_graph(ev: Evidence) -> ClaimGraph:
    graph = ClaimGraph()
    if ev.manifest.claims:
        graph.from_manifest = True
        claims = ev.manifest.claims
    else:
        # Best-effort: draft claims from evidence so the trail view exists
        # even before the author writes a manifest.
        claims = scaffold_manifest(ev).claims
    for claim in claims[:10]:
        inferred = not graph.from_manifest or (claim.status or "").strip().lower() == "draft"
        graph.trails.append(build_claim_trail(claim, ev, inferred=inferred))
    return graph
