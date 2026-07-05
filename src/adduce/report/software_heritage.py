"""Software Heritage readiness note: what an SWHID is and how to get one."""

from __future__ import annotations

from ..engine import CheckResult


def render(result: CheckResult) -> str:
    ev = result.evidence
    remotes = [r for r in result.repo.git.remotes if r.startswith("http")]
    repo_url = remotes[0] if remotes else "<your public repository URL>"
    lines = [
        "# Software Heritage archival note",
        "",
        "Software Heritage archives source code and issues SWHIDs — intrinsic, "
        "content-addressed identifiers that remain valid even if the hosting platform disappears. "
        "They complement a Zenodo DOI (which identifies a deposit, not the content).",
        "",
        "## Readiness",
        "",
        f"- Public repository: {'yes' if remotes else 'not detected — publish the repository first'}",
        f"- Tagged release to reference: {'yes' if result.repo.git.tags else 'no — tag the paper state first'}",
        f"- Repository archivable as-is: {'check R-ARC-002 in the adduce report' if ev.data.untracked_binaries else 'yes'}",
        "",
        "## Steps",
        "",
        f"1. Trigger archival: submit {repo_url} at https://archive.softwareheritage.org/save/",
        "2. Wait for the crawl to complete (usually minutes for a public GitHub repository).",
        "3. Browse to your repository on archive.softwareheritage.org and copy the SWHID of the "
        "tagged revision (swh:1:rev:...).",
        "4. Cite it in the paper alongside the DOI, e.g. in the artifact-availability statement.",
        "",
        "adduce does not submit anything on your behalf; both steps run in your browser.",
        "",
    ]
    return "\n".join(lines)
