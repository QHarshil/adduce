"""Auto-drafting the Reproducibility Manifest from detected evidence.

The scaffold is a first draft for the author to confirm and refine —
auto-linked entries are best-effort and marked ``status: draft``. Existing
manifest content is preserved: scaffolding only fills sections the author
has not written yet.
"""

from __future__ import annotations

from .evidence import Evidence
from .manifest import (
    Claim,
    DatasetInfo,
    EnvironmentInfo,
    Manifest,
    PaperInfo,
    ProducedBy,
    RemoteInfo,
    SmokeTarget,
)

_MAX_DRAFT_CLAIMS = 10


def _draft_environment(ev: Evidence) -> EnvironmentInfo:
    precision_summary = None
    if ev.precision.events:
        kinds = sorted({e.kind for e in ev.precision.events})
        precision_summary = "detected: " + ", ".join(kinds) + " (describe the policy here)"
    return EnvironmentInfo(
        python=ev.deps.python_version,
        lockfile=ev.deps.lockfiles[0] if ev.deps.lockfiles else None,
        container=ev.env.dockerfiles[0] if ev.env.dockerfiles else None,
        hardware=None,  # only the author knows what it actually ran on
        precision=precision_summary,
        cuda=None,
    )


def _draft_datasets(ev: Evidence) -> list[DatasetInfo]:
    datasets: list[DatasetInfo] = []
    seen: set[str] = set()
    for site in ev.py.call_sites_terminal("load_dataset"):
        if site.first_arg and site.first_arg not in seen:
            seen.add(site.first_arg)
            datasets.append(DatasetInfo(id=site.first_arg, source="huggingface"))
    for module in ev.py.modules:
        for site in module.calls:
            if site.qualname.startswith("torchvision.datasets."):
                name = site.qualname.rsplit(".", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    datasets.append(DatasetInfo(id=name.lower(), source="torchvision"))
    for name in sorted(ev.latex.datasets_mentioned):
        if name not in seen:
            seen.add(name)
            datasets.append(DatasetInfo(id=name))
    return datasets[:12]


def _draft_remotes(ev: Evidence) -> list[RemoteInfo]:
    return [
        RemoteInfo(call=ref.spec, revision=None)
        for ref in ev.remote.references
        if ref.kind in {"hf", "torch_hub", "sentence_transformers"} and not ref.pinned
    ][:12]


def _guess_command(ev: Evidence) -> str | None:
    if ev.runs.commands:
        return ev.runs.commands[0].command
    if ev.docs.run_commands:
        return ev.docs.run_commands[0]
    if ev.env.makefile_targets:
        preferred = [t for t in ev.env.makefile_targets if t in {"reproduce", "eval", "train", "all"}]
        target = preferred[0] if preferred else ev.env.makefile_targets[0]
        return f"make {target}"
    return None


def _draft_claims(ev: Evidence) -> list[Claim]:
    claims: list[Claim] = []
    command = _guess_command(ev)
    config = ev.config.files[0].path if ev.config.files else None
    log = ev.results.files[0].path if ev.results.files else None

    for index, metric in enumerate(ev.latex.metrics[:_MAX_DRAFT_CLAIMS], start=1):
        claims.append(
            Claim(
                id=f"C{index}",
                text=metric.raw,
                kind="metric",
                where=f"{metric.file}:{metric.line}",
                metric=metric.name,
                value=metric.value,
                produced_by=ProducedBy(command=command, config=config, log=log),
                status="draft",
            )
        )
    if not claims and ev.docs.has_results_table:
        claims.append(
            Claim(
                id="C1",
                text="Main result from the README results table (fill in the metric and value)",
                kind="metric",
                where="README results table",
                produced_by=ProducedBy(command=command, config=config, log=log),
                status="draft",
            )
        )
    return claims


def _merge_datasets(existing: list[DatasetInfo], detected: list[DatasetInfo]) -> list[DatasetInfo]:
    """Add detected datasets and fill blanks without replacing author values."""
    by_id = {dataset.id.casefold(): dataset for dataset in detected}
    merged: list[DatasetInfo] = []
    for dataset in existing:
        candidate = by_id.pop(dataset.id.casefold(), None)
        merged.append(
            DatasetInfo(
                id=dataset.id,
                source=dataset.source or (candidate.source if candidate else None),
                checksum=dataset.checksum or (candidate.checksum if candidate else None),
                split=dataset.split or (candidate.split if candidate else None),
                croissant=dataset.croissant or (candidate.croissant if candidate else None),
                license=dataset.license or (candidate.license if candidate else None),
            )
        )
    merged.extend(by_id.values())
    return merged


def _merge_remotes(existing: list[RemoteInfo], detected: list[RemoteInfo]) -> list[RemoteInfo]:
    """Add newly detected remotes while retaining confirmed revision pins."""
    by_call = {remote.call: remote for remote in detected}
    merged: list[RemoteInfo] = []
    for remote in existing:
        candidate = by_call.pop(remote.call, None)
        merged.append(
            RemoteInfo(
                call=remote.call,
                revision=remote.revision or (candidate.revision if candidate else None),
            )
        )
    merged.extend(by_call.values())
    return merged


def _claim_key(claim: Claim) -> tuple[str | None, str | None, float | None]:
    return claim.where, claim.metric, claim.value


def _merge_claims(existing: list[Claim], detected: list[Claim]) -> list[Claim]:
    """Append genuinely new draft claims without touching existing claims."""
    merged = list(existing)
    existing_keys = {_claim_key(claim) for claim in existing}
    used_ids = {claim.id for claim in existing}
    next_id = 1
    for claim in detected:
        if _claim_key(claim) in existing_keys:
            continue
        while f"C{next_id}" in used_ids:
            next_id += 1
        claim.id = f"C{next_id}"
        used_ids.add(claim.id)
        existing_keys.add(_claim_key(claim))
        merged.append(claim)
    return merged


def scaffold_manifest(ev: Evidence, refresh: bool = False) -> Manifest:
    """Build a draft manifest without replacing author-written values.

    A normal scaffold fills empty fields and empty sections. ``refresh`` also
    appends newly detected datasets, remotes, and claims to populated
    sections. Neither mode removes an entry or replaces a non-empty value.
    """
    existing = ev.manifest
    detected_paper = PaperInfo(title=ev.latex.title, file=ev.latex.main_file)
    detected_environment = _draft_environment(ev)
    detected_datasets = _draft_datasets(ev)
    detected_remotes = _draft_remotes(ev)
    detected_claims = _draft_claims(ev)

    datasets = existing.datasets or detected_datasets
    remotes = existing.remotes or detected_remotes
    claims = existing.claims or detected_claims
    if refresh:
        datasets = _merge_datasets(existing.datasets, detected_datasets)
        remotes = _merge_remotes(existing.remotes, detected_remotes)
        claims = _merge_claims(existing.claims, detected_claims)

    return Manifest(
        paper=PaperInfo(
            title=existing.paper.title or detected_paper.title,
            file=existing.paper.file or detected_paper.file,
        ),
        environment=EnvironmentInfo(
            python=existing.environment.python or detected_environment.python,
            lockfile=existing.environment.lockfile or detected_environment.lockfile,
            container=existing.environment.container or detected_environment.container,
            hardware=existing.environment.hardware or detected_environment.hardware,
            precision=existing.environment.precision or detected_environment.precision,
            cuda=existing.environment.cuda or detected_environment.cuda,
        ),
        datasets=datasets,
        remotes=remotes,
        claims=claims,
        smoke=existing.smoke if existing.smoke.command else SmokeTarget(),
    )
