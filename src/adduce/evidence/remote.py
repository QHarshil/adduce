"""Remote-artifact evidence: model hub calls, dataset downloads, raw URLs.

Detection is fully offline: this collector only reads source. Whether a
reference is *pinned* is judged from the source itself (a 40-hex ``revision``
is immutable; a branch or tag is not). Resolution of current SHAs is a
separate, opt-in online step (``adduce pin-remotes``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..model import Repo
from .python_ast import PythonEvidence

if TYPE_CHECKING:
    from ..dynamic.resolve import Resolution

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_URL_RE = re.compile(r"(?:wget|curl)\s+(?:-\S+\s+)*['\"]?(https?://\S+?)['\"]?(?:\s|$)")
_GDOWN_RE = re.compile(r"gdown(?:\.download)?\s*[( ]\s*['\"]?(https?://drive\.google\.com/\S+|[\w-]{20,})")
_BUCKET_RE = re.compile(r"\b(s3://[\w\-./]+|gs://[\w\-./]+)")
_DRIVE_URL_RE = re.compile(r"https?://drive\.google\.com/\S+")
_DOWNLOAD_TARGET_RE = re.compile(
    r"(?:^|\s)(?:-o|--output(?:-document)?)(?:=|\s+)['\"]?([^\s'\";|]+)",
    re.IGNORECASE,
)
_CHECKSUM_VERIFY_RE = re.compile(
    r"\b(?:(?:sha256sum|sha512sum)\s+(?:--check|-c)|"
    r"shasum\s+-a\s+(?:256|512)\s+(?:--check|-c))\b",
    re.IGNORECASE,
)

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
    resolver_kind: str | None = None  # hf-model | hf-dataset | github


@dataclass
class RemoteEvidence:
    references: list[RemoteRef] = field(default_factory=list)
    online_attempted: bool = False
    resolutions: list[Resolution] = field(default_factory=list)

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
    for terminal in sorted(_HF_TERMINALS):
        for site in py.call_sites_terminal(terminal):
            pinned, detail = _classify_revision(site.kw_value("revision"))
            target = f'"{site.first_arg}"' if site.first_arg else "..."
            resolver_kind = (
                "hf-dataset"
                if terminal == "load_dataset" or site.kw_value("repo_type") == "'dataset'"
                else "hf-model"
            )
            evidence.references.append(
                RemoteRef(
                    kind="hf",
                    spec=f"{site.qualname}({target})",
                    file=site.file,
                    line=site.line,
                    pinned=pinned,
                    pin_detail=detail,
                    resolver_kind=resolver_kind,
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
                resolver_kind="github",
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
                resolver_kind="hf-model",
            )
        )


def _download_has_bound_checksum(lines: list[str], index: int) -> bool:
    """Recognise a checksum command visibly bound to this download.

    A repository-global checksum file is not enough: it may cover a different
    artifact.  Accept a checksum pipeline on the download line, or a checksum
    command in the next three lines that names the explicit download target.
    """
    line = lines[index]
    if "|" in line and _CHECKSUM_VERIFY_RE.search(line.split("|", 1)[1]):
        return True
    target_match = _DOWNLOAD_TARGET_RE.search(line)
    if target_match is None:
        return False
    target = target_match.group(1)
    target_name = target.rsplit("/", 1)[-1]
    for candidate in lines[index + 1 : index + 4]:
        if _URL_RE.search(candidate) or _GDOWN_RE.search(candidate) or _BUCKET_RE.search(candidate):
            break
        if _CHECKSUM_VERIFY_RE.search(candidate) and (
            target in candidate or target_name in candidate
        ):
            return True
    return False


def _collect_from_text(repo: Repo, evidence: RemoteEvidence) -> None:
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
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            checksum_bound = _download_has_bound_checksum(lines, lineno - 1)
            for match in _URL_RE.finditer(line):
                url = match.group(1)
                kind = "gdrive" if "drive.google.com" in url else "url"
                evidence.references.append(
                    RemoteRef(
                        kind=kind,
                        spec=url,
                        file=rel,
                        line=lineno,
                        pinned=checksum_bound,
                        pin_detail="checksum" if checksum_bound else "none",
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
                        pinned=checksum_bound,
                        pin_detail="checksum" if checksum_bound else "none",
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


def collect_remote(repo: Repo, py: PythonEvidence) -> RemoteEvidence:
    """Collect remote references and reference-bound integrity evidence."""
    evidence = RemoteEvidence()
    _collect_from_ast(py, evidence)
    _collect_from_text(repo, evidence)
    evidence.references.sort(
        key=lambda reference: (
            reference.file,
            reference.line,
            reference.kind,
            reference.spec,
            reference.pin_detail,
        )
    )
    return evidence
