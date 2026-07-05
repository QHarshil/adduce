"""Remote-artifact evidence: model hub calls, dataset downloads, raw URLs.

Detection is fully offline: this collector only reads source. Whether a
reference is *pinned* is judged from the source itself (a 40-hex ``revision``
is immutable; a branch or tag is not). Resolution of current SHAs is a
separate, opt-in online step (``adduce pin-remotes``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..model import Repo
from .python_ast import PythonEvidence

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_URL_RE = re.compile(r"(?:wget|curl)\s+(?:-\S+\s+)*['\"]?(https?://\S+?)['\"]?(?:\s|$)")
_GDOWN_RE = re.compile(r"gdown(?:\.download)?\s*[( ]\s*['\"]?(https?://drive\.google\.com/\S+|[\w-]{20,})")
_BUCKET_RE = re.compile(r"\b(s3://[\w\-./]+|gs://[\w\-./]+)")
_DRIVE_URL_RE = re.compile(r"https?://drive\.google\.com/\S+")

#: Call terminals that fetch remote artifacts and accept a revision pin.
_HF_TERMINALS = frozenset({"from_pretrained", "load_dataset", "hf_hub_download", "snapshot_download"})


@dataclass(frozen=True)
class RemoteRef:
    kind: str          # hf | torch_hub | url | gdrive | bucket | sentence_transformers
    spec: str          # the call or URL as written
    file: str
    line: int
    pinned: bool
    pin_detail: str    # "sha" | "mutable-ref" | "checksum" | "none"


@dataclass
class RemoteEvidence:
    references: list[RemoteRef] = field(default_factory=list)

    @property
    def unpinned(self) -> list[RemoteRef]:
        return [r for r in self.references if not r.pinned]

    def by_kind(self, kind: str) -> list[RemoteRef]:
        return [r for r in self.references if r.kind == kind]


def _classify_revision(value: str | None) -> tuple[bool, str]:
    if value is None:
        return False, "none"
    cleaned = value.strip("'\"")
    if _SHA_RE.match(cleaned):
        return True, "sha"
    return False, "mutable-ref"


def _collect_from_ast(py: PythonEvidence, evidence: RemoteEvidence) -> None:
    for terminal in _HF_TERMINALS:
        for site in py.call_sites_terminal(terminal):
            pinned, detail = _classify_revision(site.kw_value("revision"))
            target = f'"{site.first_arg}"' if site.first_arg else "..."
            evidence.references.append(
                RemoteRef(
                    kind="hf",
                    spec=f"{site.qualname}({target})",
                    file=site.file,
                    line=site.line,
                    pinned=pinned,
                    pin_detail=detail,
                )
            )
    for site in py.call_sites("torch.hub.load"):
        # torch.hub.load("owner/repo:ref", ...): only a 40-hex ref is immutable.
        pinned, detail = False, "none"
        if site.first_arg and ":" in site.first_arg:
            ref = site.first_arg.rsplit(":", 1)[-1]
            if _SHORT_SHA_RE.match(ref) and len(ref) == 40:
                pinned, detail = True, "sha"
            else:
                detail = "mutable-ref"
        evidence.references.append(
            RemoteRef(
                kind="torch_hub",
                spec=f'torch.hub.load("{site.first_arg}")' if site.first_arg else "torch.hub.load(...)",
                file=site.file,
                line=site.line,
                pinned=pinned,
                pin_detail=detail,
            )
        )
    for site in py.call_sites_terminal("SentenceTransformer"):
        pinned, detail = _classify_revision(site.kw_value("revision"))
        target = f'"{site.first_arg}"' if site.first_arg else "..."
        evidence.references.append(
            RemoteRef(
                kind="sentence_transformers",
                spec=f"{site.qualname}({target})",
                file=site.file,
                line=site.line,
                pinned=pinned,
                pin_detail=detail,
            )
        )


def _collect_from_text(repo: Repo, evidence: RemoteEvidence, checksum_nearby: bool) -> None:
    scannable = [
        f
        for f in repo.files
        if f.suffix in {".sh", ".bash", ".py"} or f.name in {"Makefile", "makefile"}
    ]
    for entry in scannable:
        text = repo.read_text(entry.path)
        if text is None:
            continue
        rel = str(entry.path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _URL_RE.finditer(line):
                url = match.group(1)
                kind = "gdrive" if "drive.google.com" in url else "url"
                evidence.references.append(
                    RemoteRef(
                        kind=kind,
                        spec=url[:200],
                        file=rel,
                        line=lineno,
                        pinned=checksum_nearby,
                        pin_detail="checksum" if checksum_nearby else "none",
                    )
                )
            if _GDOWN_RE.search(line):
                evidence.references.append(
                    RemoteRef(
                        kind="gdrive",
                        spec=line.strip()[:200],
                        file=rel,
                        line=lineno,
                        pinned=False,
                        pin_detail="none",
                    )
                )
            for match in _BUCKET_RE.finditer(line):
                evidence.references.append(
                    RemoteRef(
                        kind="bucket",
                        spec=match.group(1)[:200],
                        file=rel,
                        line=lineno,
                        pinned=checksum_nearby,
                        pin_detail="checksum" if checksum_nearby else "none",
                    )
                )
            bare_drive_link = (
                entry.suffix in {".sh", ".bash"}
                and _DRIVE_URL_RE.search(line)
                and not any(tool in line for tool in ("wget", "curl", "gdown"))
            )
            if bare_drive_link:
                evidence.references.append(
                    RemoteRef(
                        kind="gdrive",
                        spec=line.strip()[:200],
                        file=rel,
                        line=lineno,
                        pinned=False,
                        pin_detail="none",
                    )
                )


def collect_remote(repo: Repo, py: PythonEvidence, has_checksums: bool) -> RemoteEvidence:
    """``has_checksums``: whether the repo ships checksum files that could
    cover raw-URL downloads (grants URL references the benefit of the doubt)."""
    evidence = RemoteEvidence()
    _collect_from_ast(py, evidence)
    _collect_from_text(repo, evidence, checksum_nearby=has_checksums)
    return evidence
