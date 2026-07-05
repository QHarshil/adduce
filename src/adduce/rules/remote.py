"""Remote artifacts & rot: will the models and files this repo fetches still
be the same next year?

Detection is offline. Resolution to current SHAs is the opt-in online step
(``adduce pin-remotes``), and even then pinning to the *current* SHA is a
forward guarantee, not recovery of the version historically used — the
wording keeps that distinction explicit.
"""

from __future__ import annotations

from ..evidence import Evidence
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status


class HFRevisionRule(Rule):
    id = "R-REMOTE-001"
    category = Category.REMOTE
    title = "Hugging Face references carry a revision pin"
    rationale = (
        "from_pretrained/load_dataset without revision= float on the hub's main branch; "
        "the artifact silently changes when upstream pushes."
    )
    weight = 4
    fix_command = "adduce pin-remotes --diff"

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:
        refs = ev.remote.by_kind("hf") + ev.remote.by_kind("sentence_transformers")
        if not refs:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No Hugging Face hub calls detected.")
        unpinned = [r for r in refs if not r.pinned]
        if not unpinned:
            return self.finding(
                Status.PASS, confidence=0.85, message=f"All {len(refs)} hub call(s) pin an immutable revision."
            )
        examples = "; ".join(r.spec for r in unpinned[:3])
        return self.finding(
            Status.PARTIAL if len(unpinned) < len(refs) else Status.FAIL,
            confidence=0.85,
            message=f"{len(unpinned)} of {len(refs)} hub call(s) have no immutable revision pin: {examples}.",
            remediation=(
                "Add revision=\"<commit-sha>\" to each call. `adduce pin-remotes --diff` resolves current SHAs "
                "(online) and drafts the edits — note this pins the current version, which may differ from the "
                "version originally used; verify before trusting."
            ),
            locations=[Location(r.file, r.line) for r in unpinned[:5]],
        )


class MutableRevisionRule(Rule):
    id = "R-REMOTE-002"
    category = Category.REMOTE
    title = "Revision pins are commit SHAs, not branches or tags"
    rationale = "A branch or tag revision moves; only a commit SHA is immutable on the hub."
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:
        refs = [r for r in ev.remote.references if r.kind in {"hf", "sentence_transformers"}]
        if not refs:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No Hugging Face hub calls detected.")
        mutable = [r for r in refs if r.pin_detail == "mutable-ref"]
        if not mutable:
            return self.finding(
                Status.PASS, confidence=0.8, message="No mutable-ref (branch/tag) revisions detected on hub calls."
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.8,
            message=f"{len(mutable)} hub call(s) pin a branch or tag rather than a commit SHA.",
            remediation="Replace branch/tag revisions with the 40-hex commit SHA (`adduce pin-remotes --diff` resolves them).",
            locations=[Location(r.file, r.line) for r in mutable[:5]],
        )


class TorchHubRule(Rule):
    id = "R-REMOTE-003"
    category = Category.REMOTE
    title = "torch.hub.load pinned to a commit"
    rationale = "torch.hub.load('owner/repo') tracks the default branch of a GitHub repo — maximal rot exposure."
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        refs = ev.remote.by_kind("torch_hub")
        if not refs:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No torch.hub.load calls detected.")
        unpinned = [r for r in refs if not r.pinned]
        if not unpinned:
            return self.finding(Status.PASS, confidence=0.8, message="torch.hub.load calls pin a commit.")
        return self.finding(
            Status.PARTIAL if len(unpinned) < len(refs) else Status.FAIL,
            confidence=0.8,
            message=f"{len(unpinned)} torch.hub.load call(s) track a branch or tag.",
            remediation='Use torch.hub.load("owner/repo:<40-hex-commit>", ...) so the loaded code cannot drift.',
            locations=[Location(r.file, r.line) for r in unpinned[:5]],
        )


class RawUrlRule(Rule):
    id = "R-REMOTE-004"
    category = Category.REMOTE
    title = "Raw URL / drive / bucket downloads carry integrity checks"
    rationale = (
        "A wget with no checksum fetches whatever the server serves that day; with a checksum "
        "it fetches the artifact or fails loudly."
    )
    weight = 3

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:
        refs = [r for r in ev.remote.references if r.kind in {"url", "gdrive", "bucket"}]
        if not refs:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No raw URL/drive/bucket downloads detected.")
        unchecked = [r for r in refs if r.pin_detail != "checksum"]
        if not unchecked:
            return self.finding(
                Status.PASS,
                confidence=0.6,
                message=f"{len(refs)} raw download(s) detected and checksum files exist to verify them.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message=f"{len(unchecked)} raw download(s) have no checksum coverage.",
            remediation="Record SHA-256 checksums for each downloaded artifact and verify after download.",
            locations=[Location(r.file, r.line) for r in unchecked[:5]],
        )


class RemoteResolutionRule(Rule):
    id = "R-REMOTE-005"
    category = Category.REMOTE
    title = "Online resolution of remote references (opt-in)"
    rationale = (
        "With --online, adduce resolves current hub revisions and URL heads from the user's "
        "machine; failures here mean the remote is gone, gated, or private — rot has already begun."
    )
    weight = 1

    def applies_to(self, repo: Repo) -> bool:
        return True

    def evaluate(self, ev: Evidence) -> Finding:
        # The offline default never performs resolution; the pin-remotes command
        # attaches its outcome here when run with --online.
        if not ev.remote.references:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.7, message="No remote references to resolve.")
        return self.finding(
            Status.UNKNOWN,
            confidence=0.9,
            message=f"{len(ev.remote.references)} remote reference(s) detected; resolution requires the opt-in "
            "online step (`adduce pin-remotes` or `adduce check --online`).",
            remediation="Run `adduce pin-remotes` to resolve current revisions from your machine (public metadata only).",
        )
