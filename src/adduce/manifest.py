"""The Reproducibility Manifest: ``.adduce/manifest.yaml``.

The manifest is the machine-readable source of truth for claim traceability.
``adduce manifest`` scaffolds it from detected evidence, the author refines
it, and every other command consumes it. Manifest-declared links are
authoritative; links inferred from evidence carry confidence instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "adduce/1"
MANIFEST_DIR = ".adduce"
MANIFEST_NAME = "manifest.yaml"


@dataclass
class PaperInfo:
    title: str | None = None
    file: str | None = None


@dataclass
class EnvironmentInfo:
    python: str | None = None
    lockfile: str | None = None
    container: str | None = None
    hardware: str | None = None
    precision: str | None = None
    cuda: str | None = None


@dataclass
class DatasetInfo:
    id: str
    source: str | None = None
    checksum: str | None = None
    split: str | None = None
    croissant: str | None = None
    license: str | None = None


@dataclass
class RemoteInfo:
    call: str
    revision: str | None = None


@dataclass
class ProducedBy:
    command: str | None = None
    config: str | None = None
    data: str | None = None
    log: str | None = None
    commit: str | None = None


@dataclass
class Claim:
    id: str
    text: str | None = None
    kind: str = "metric"          # metric | figure | table | statement
    where: str | None = None      # "Table 2", "Section 5.1"
    metric: str | None = None
    value: float | None = None
    seeds: list[int] = field(default_factory=list)
    produced_by: ProducedBy = field(default_factory=ProducedBy)
    status: str | None = None


@dataclass
class SmokeTarget:
    command: str | None = None
    max_runtime_minutes: int | None = None
    expected_outputs: list[str] = field(default_factory=list)
    expected_metrics: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    paper: PaperInfo = field(default_factory=PaperInfo)
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    datasets: list[DatasetInfo] = field(default_factory=list)
    remotes: list[RemoteInfo] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    smoke: SmokeTarget = field(default_factory=SmokeTarget)
    path: Path | None = None  # where it was loaded from, if anywhere

    @property
    def exists(self) -> bool:
        return self.path is not None

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if v not in (None, [], {})}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        raw: dict[str, Any] = {
            "schema": SCHEMA,
            "paper": {"title": self.paper.title, "file": self.paper.file},
            "environment": {
                "python": self.environment.python,
                "lockfile": self.environment.lockfile,
                "container": self.environment.container,
                "hardware": self.environment.hardware,
                "precision": self.environment.precision,
                "cuda": self.environment.cuda,
            },
            "datasets": [
                {
                    "id": d.id,
                    "source": d.source,
                    "checksum": d.checksum,
                    "split": d.split,
                    "croissant": d.croissant,
                    "license": d.license,
                }
                for d in self.datasets
            ],
            "remotes": [{"call": r.call, "revision": r.revision} for r in self.remotes],
            "claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "kind": c.kind,
                    "where": c.where,
                    "metric": c.metric,
                    "value": c.value,
                    "seeds": c.seeds,
                    "produced_by": {
                        "command": c.produced_by.command,
                        "config": c.produced_by.config,
                        "data": c.produced_by.data,
                        "log": c.produced_by.log,
                        "commit": c.produced_by.commit,
                    },
                    "status": c.status,
                }
                for c in self.claims
            ],
            "smoke": {
                "command": self.smoke.command,
                "max_runtime_minutes": self.smoke.max_runtime_minutes,
                "expected_outputs": self.smoke.expected_outputs,
                "expected_metrics": self.smoke.expected_metrics,
            },
        }
        return clean(raw)


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _parse_claim(raw: dict[str, Any]) -> Claim:
    produced = raw.get("produced_by") or {}
    value = raw.get("value")
    return Claim(
        id=str(raw.get("id", "")),
        text=_as_str(raw.get("text")),
        kind=str(raw.get("kind", "metric")),
        where=_as_str(raw.get("where")),
        metric=_as_str(raw.get("metric")),
        value=float(value) if isinstance(value, (int, float)) else None,
        seeds=[int(s) for s in raw.get("seeds", []) if isinstance(s, (int, float))],
        produced_by=ProducedBy(
            command=_as_str(produced.get("command")),
            config=_as_str(produced.get("config")),
            data=_as_str(produced.get("data")),
            log=_as_str(produced.get("log")),
            commit=_as_str(produced.get("commit")),
        ),
        status=_as_str(raw.get("status")),
    )


def load_manifest(root: Path) -> Manifest:
    """Load the manifest if present; otherwise an empty manifest (exists=False)."""
    target = root / MANIFEST_DIR / MANIFEST_NAME
    if not target.is_file():
        return Manifest()
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return Manifest()
    if not isinstance(data, dict):
        return Manifest()

    paper = data.get("paper") or {}
    env = data.get("environment") or {}
    smoke = data.get("smoke") or {}
    manifest = Manifest(
        paper=PaperInfo(title=_as_str(paper.get("title")), file=_as_str(paper.get("file"))),
        environment=EnvironmentInfo(
            python=_as_str(env.get("python")),
            lockfile=_as_str(env.get("lockfile")),
            container=_as_str(env.get("container")),
            hardware=_as_str(env.get("hardware")),
            precision=_as_str(env.get("precision")),
            cuda=_as_str(env.get("cuda")),
        ),
        datasets=[
            DatasetInfo(
                id=str(d.get("id", f"dataset-{i}")),
                source=_as_str(d.get("source")),
                checksum=_as_str(d.get("checksum")),
                split=_as_str(d.get("split")),
                croissant=_as_str(d.get("croissant")),
                license=_as_str(d.get("license")),
            )
            for i, d in enumerate(data.get("datasets") or [])
            if isinstance(d, dict)
        ],
        remotes=[
            RemoteInfo(call=str(r.get("call", "")), revision=_as_str(r.get("revision")))
            for r in (data.get("remotes") or [])
            if isinstance(r, dict)
        ],
        claims=[_parse_claim(c) for c in (data.get("claims") or []) if isinstance(c, dict)],
        smoke=SmokeTarget(
            command=_as_str(smoke.get("command")),
            max_runtime_minutes=int(smoke["max_runtime_minutes"])
            if isinstance(smoke.get("max_runtime_minutes"), (int, float))
            else None,
            expected_outputs=[str(o) for o in smoke.get("expected_outputs", [])],
            expected_metrics=[str(m) for m in smoke.get("expected_metrics", [])],
        ),
        path=target,
    )
    return manifest


def write_manifest(root: Path, manifest: Manifest) -> Path:
    """Serialise the manifest to ``.adduce/manifest.yaml`` plus a JSON mirror."""
    import json

    directory = root / MANIFEST_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MANIFEST_NAME
    payload = manifest.to_dict()
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (directory / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    manifest.path = target
    return target
