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


def scaffold_manifest(ev: Evidence) -> Manifest:
    """Build a draft manifest from evidence, preserving any existing content."""
    existing = ev.manifest
    return Manifest(
        paper=existing.paper if existing.paper.title or existing.paper.file else PaperInfo(
            title=ev.latex.title,
            file=ev.latex.main_file,
        ),
        environment=existing.environment
        if any(
            [existing.environment.python, existing.environment.container, existing.environment.hardware]
        )
        else _draft_environment(ev),
        datasets=existing.datasets or _draft_datasets(ev),
        remotes=existing.remotes or _draft_remotes(ev),
        claims=existing.claims or _draft_claims(ev),
        smoke=existing.smoke if existing.smoke.command else SmokeTarget(),
    )
