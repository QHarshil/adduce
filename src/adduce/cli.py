"""Command-line interface.

One simple entrypoint (``adduce check``) runs everything safe and local;
focused subcommands add depth. Anything online or executing repository code
is a separate, opt-in command and says so.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__
from .checklists import available_checklists, load_checklist, render_markdown
from .engine import (
    BASELINE_FILENAME,
    CheckResult,
    baseline_snapshot,
    regressions_against,
    run_check,
)
from .fixers import RULE_TO_SCAFFOLD, SCAFFOLDS
from .ledger import (
    LEDGER_DIR,
    LEDGER_NAME,
    Ledger,
    sha256_file,
    write_ledger,
)
from .manifest import write_manifest, write_manifest_proposal
from .manifest_builder import scaffold_manifest
from .modes import Mode
from .profiles import available_profiles
from .report import RENDERERS
from .report import appendix as appendix_report
from .report import badge as badge_report
from .report import checksums as checksums_report
from .report import codemeta as codemeta_report
from .report import croissant as croissant_report
from .report import ro_crate as ro_crate_report
from .report import software_heritage as swh_report
from .report import terminal as terminal_report
from .report import zenodo as zenodo_report
from .rules import Category, Status, discover_rules

app = typer.Typer(
    name="adduce",
    help=(
        "A local research-artifact auditor: checks that a paper's claims, code, configs, "
        "data, dependencies, remote models, precision settings, and results still agree "
        "with each other, and produces the artifacts reviewers ask for."
    ),
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

_FORMATS = ("terminal", *RENDERERS.keys())


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"adduce {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
    ] = None,
) -> None:
    """adduce is offline by default: it reads your repository and sends nothing anywhere.

    It reports detected signals; it never certifies reproducibility."""


def _run(
    path: Path,
    profile: str | None = None,
    ignore: list[str] | None = None,
    exclude: list[str] | None = None,
    only: list[str] | None = None,
    skip: list[str] | None = None,
    paper: Path | None = None,
) -> CheckResult:
    if not path.is_dir():
        err_console.print(f"[red]error:[/red] {path} is not a directory")
        raise typer.Exit(code=2)
    if paper is not None and not paper.exists():
        err_console.print(f"[red]error:[/red] --paper path {paper} does not exist")
        raise typer.Exit(code=2)
    rules = None
    if only or skip:
        rules = discover_rules()
        if only:
            prefixes = tuple(p.upper() for p in only)
            rules = [r for r in rules if r.id.startswith(prefixes)]
        if skip:
            prefixes = tuple(p.upper() for p in skip)
            rules = [r for r in rules if not r.id.startswith(prefixes)]
    try:
        return run_check(
            path,
            profile_name=profile,
            ignore=frozenset(ignore or []),
            exclude=tuple(exclude or []),
            rules=rules,
            paper=paper,
        )
    except ValueError as exc:  # unknown profile, malformed config
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _write_or_print(rendered: str, output: Path | None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        err_console.print(f"written to {output}")
    else:
        sys.stdout.write(rendered.rstrip("\n") + "\n")


def _ledger_key(output: Path | None, root: Path, default: str) -> str:
    """Ledger key for a generated artifact: root-relative when possible.

    Root-relative keys let ``audit-generated`` find the record from the same
    repository regardless of the working directory the artifact was made from.
    """
    if output is None:
        return default
    try:
        return output.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return output.name


def _print_generation_summary(counts: dict[str, int], ledger_path: Path) -> None:
    """Summarise what the draft rests on — to stderr, never into the artifact."""
    err_console.print(
        "generation summary: "
        f"{counts['evidence_backed']} evidence-backed, {counts['partial']} partial, "
        f"{counts['author_input_required']} author input required, "
        f"{counts['not_detected']} not detected, {counts['unknown']} unknown, "
        f"{counts['conflicts']} conflict(s)"
    )
    err_console.print(f"ledger: {ledger_path}")
    if counts["partial"] or counts["author_input_required"] or counts["conflicts"]:
        err_console.print("Review required before submission — this draft is not submission-ready.")


def _print_category_findings(result: CheckResult, categories: set[Category]) -> None:
    table = Table(box=None, pad_edge=False, header_style="bold dim")
    table.add_column("Rule")
    table.add_column("Status")
    table.add_column("Confidence", justify="right")
    table.add_column("Detail", overflow="fold")
    shown = 0
    for finding in result.card.findings:
        if finding.category not in categories:
            continue
        shown += 1
        style = {"pass": "green", "partial": "yellow", "fail": "red"}.get(finding.status.value, "dim")
        detail = finding.message
        if finding.locations:
            detail += "\n  at " + ", ".join(str(loc) for loc in finding.locations[:4])
        if finding.remediation and finding.status not in (Status.PASS, Status.NOT_APPLICABLE):
            detail += f"\n  fix: {finding.remediation}"
        table.add_row(finding.rule_id, Text(finding.status.value, style=style), f"{finding.confidence:.0%}", detail)
    if shown:
        console.print(table)
    else:
        console.print(Text("no applicable findings", style="dim"))


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


@app.command()
def check(
    path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path("."),
    profile: Annotated[
        str | None,
        typer.Option(help=f"Scoring profile: {', '.join(available_profiles())}, or a path to a profile TOML."),
    ] = None,
    mode: Annotated[Mode, typer.Option(help="Report framing: author (fix-oriented), reviewer (skeptical), ae-chair (badges and burden).")] = Mode.AUTHOR,
    output_format: Annotated[str, typer.Option("--format", "-f", help=f"Output format: {', '.join(_FORMATS)}.")] = "terminal",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the report to a file instead of stdout.")] = None,
    fail_under: Annotated[
        float | None,
        typer.Option(help="Exit non-zero when the total score is below this threshold (CI gate; off by default)."),
    ] = None,
    fail_on_regression: Annotated[
        bool, typer.Option(help=f"Exit non-zero when any rule is worse than the recorded {BASELINE_FILENAME}.")
    ] = False,
    online: Annotated[
        bool,
        typer.Option(help="Opt-in: also resolve public remote metadata (Hugging Face revisions, URL heads) from this machine."),
    ] = False,
    only: Annotated[list[str] | None, typer.Option("--only", help="Run only rules with this ID prefix (repeatable), e.g. R-DET.")] = None,
    skip: Annotated[list[str] | None, typer.Option("--skip", help="Skip rules with this ID prefix (repeatable).")] = None,
    ignore: Annotated[list[str] | None, typer.Option("--ignore", help="Rule ID to suppress (repeatable).")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Directory name to skip while scanning (repeatable).")] = None,
    paper: Annotated[
        Path | None,
        typer.Option("--paper", help="LaTeX sources kept outside this repository (a directory or a .tex file)."),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show every finding, not just the summary.")] = False,
) -> None:
    """Scan a repository and report its reproducibility posture (offline)."""
    if output_format not in _FORMATS:
        err_console.print(f"[red]error:[/red] unknown format '{output_format}'. Choose from: {', '.join(_FORMATS)}.")
        raise typer.Exit(code=2)

    result = _run(path, profile, ignore, exclude, only, skip, paper=paper)

    if output_format == "terminal":
        terminal_report.render(result, console, verbose=verbose, mode=mode)
    else:
        _write_or_print(RENDERERS[output_format](result), output)

    if online:
        # Online diagnostics go to stderr so JSON/SARIF/Markdown written to
        # stdout remain valid machine-readable documents.
        _resolve_and_print(result, output_console=err_console)

    exit_code = 0
    threshold = fail_under if fail_under is not None else result.config.fail_under
    if threshold is not None and result.card.total < threshold:
        err_console.print(f"[red]score {result.card.total:.0f} is below --fail-under {threshold:.0f}[/red]")
        exit_code = 1
    if fail_on_regression:
        baseline_path = path / BASELINE_FILENAME
        if not baseline_path.is_file():
            err_console.print(
                f"[yellow]no {BASELINE_FILENAME} found; run `adduce baseline` first. Not failing.[/yellow]"
            )
        else:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            regressed = regressions_against(result.card, baseline)
            if regressed:
                err_console.print("[red]regressions against baseline:[/red]")
                for finding in regressed:
                    err_console.print(f"  {finding.rule_id} → {finding.status.value}: {finding.message}")
                exit_code = 1
    raise typer.Exit(code=exit_code)


# --------------------------------------------------------------------------
# focused audits
# --------------------------------------------------------------------------


@app.command()
def drift(
    path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path("."),
    paper: Annotated[
        Path | None,
        typer.Option("--paper", help="LaTeX sources kept outside this repository (a directory or a .tex file)."),
    ] = None,
) -> None:
    """Paper ↔ code/config consistency plus result reconciliation (offline)."""
    result = _run(path, paper=paper)
    if not result.evidence.latex.has_paper and not result.evidence.manifest.claims:
        console.print("no .tex sources or manifest claims found; nothing to compare the artifact against.")
        raise typer.Exit()
    _print_category_findings(result, {Category.DRIFT, Category.RESULTS, Category.RUN})


@app.command()
def precision(path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path(".")) -> None:
    """TF32/AMP/low-precision audit: what the code does vs what is documented (offline)."""
    result = _run(path)
    events = result.evidence.precision.events
    if events:
        console.print(f"[bold]Detected precision controls[/bold] ({len(events)}):")
        for event in events[:20]:
            console.print(f"  {event.file}:{event.line}  {event.detail}")
        console.print()
    _print_category_findings(result, {Category.PRECISION})


@app.command()
def deps(path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path(".")) -> None:
    """Dependency hygiene: ghost imports, unused declarations, notebook-only imports (offline)."""
    result = _run(path)
    _print_category_findings(result, {Category.DEPENDENCIES, Category.ENVIRONMENT})


# --------------------------------------------------------------------------
# manifest / checklist / appendix / exports
# --------------------------------------------------------------------------


@app.command()
def manifest(
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            "--force",
            help=(
                "Write a proposed refresh beside an existing manifest for review; never overwrite it. "
                "--force is retained as a deprecated compatibility alias."
            )
        ),
    ] = False,
    paper: Annotated[
        Path | None,
        typer.Option("--paper", help="LaTeX sources kept outside this repository (a directory or a .tex file)."),
    ] = None,
) -> None:
    """Scaffold or refresh .adduce/manifest.yaml from detected evidence (offline)."""
    result = _run(path, paper=paper)
    if result.evidence.manifest.exists and not refresh:
        console.print(
            f"manifest already exists at {result.evidence.manifest.path}; left unchanged. "
            "Use --refresh to write a separate proposal."
        )
        raise typer.Exit()
    draft = scaffold_manifest(result.evidence, refresh=refresh)
    if result.evidence.manifest.exists:
        target = write_manifest_proposal(path, draft)
        console.print(f"manifest refresh proposal written to {target}; the existing manifest was unchanged")
    else:
        target = write_manifest(path, draft)
        console.print(f"manifest written to {target}")
    console.print(
        f"  {len(draft.claims)} draft claim(s), {len(draft.datasets)} dataset(s), "
        f"{len(draft.remotes)} unpinned remote(s) recorded"
    )
    console.print("review every 'draft' entry: auto-linked edges are best-effort, the manifest is authoritative once you confirm it.")


@app.command()
def checklist(
    path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path("."),
    profile: Annotated[
        str, typer.Option(help=f"Checklist: {', '.join(available_checklists())}, or a path to a checklist YAML.")
    ] = "neurips",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout.")] = None,
    llm: Annotated[
        bool,
        typer.Option(help="Draft free-text justifications with your configured LLM (BYO-key; evidence answers stay deterministic)."),
    ] = False,
    strict_evidence: Annotated[
        bool,
        typer.Option(
            "--strict-evidence",
            help="Raise the evidence bar: a drafted yes needs stronger detected signals, and inferred-only items go back to the author.",
        ),
    ] = False,
    paper: Annotated[
        Path | None,
        typer.Option("--paper", help="LaTeX sources kept outside this repository (a directory or a .tex file)."),
    ] = None,
) -> None:
    """Draft a conference reproducibility checklist from repository evidence (offline unless --llm)."""
    try:
        selected = load_checklist(profile)
    except ValueError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    result = _run(path, paper=paper)
    llm_drafts: dict[str, str] = {}
    if llm:
        from . import llm as llm_module

        findings_by_rule = {f.rule_id: f for f in result.card.findings}
        for item in selected.items:
            evidence_lines = [
                f"{findings_by_rule[r].status.value}: {findings_by_rule[r].message}"
                for r in item.rules
                if r in findings_by_rule
            ]
            if not evidence_lines:
                continue
            try:
                llm_drafts[item.id] = llm_module.draft_justification(item.question, evidence_lines)
            except llm_module.LLMUnavailable as exc:
                err_console.print(f"[yellow]LLM drafting skipped:[/yellow] {exc}")
                break
    rendered, ledger = render_markdown(selected, result, llm_drafts=llm_drafts, strict=strict_evidence)
    _write_or_print(rendered, output)
    ledger.artifact_path = _ledger_key(output, path, ledger.artifact_path)
    ledger_path = write_ledger(path, ledger)
    _print_generation_summary(ledger.counts(), ledger_path)


@app.command()
def appendix(
    path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout.")] = None,
    strict_evidence: Annotated[
        bool,
        typer.Option(
            "--strict-evidence",
            help="Raise the evidence bar: a drafted yes needs stronger detected signals, and inferred-only items go back to the author.",
        ),
    ] = False,
    paper: Annotated[
        Path | None,
        typer.Option("--paper", help="LaTeX sources kept outside this repository (a directory or a .tex file)."),
    ] = None,
) -> None:
    """Draft an ACM Artifact Appendix from repository evidence (offline)."""
    result = _run(path, paper=paper)
    rendered, ledger = appendix_report.render(result, strict=strict_evidence)
    _write_or_print(rendered, output)
    ledger.artifact_path = _ledger_key(output, path, ledger.artifact_path)
    ledger_path = write_ledger(path, ledger)
    _print_generation_summary(ledger.counts(), ledger_path)


# --------------------------------------------------------------------------
# generation safety: audit-generated / package
# --------------------------------------------------------------------------

_EXECUTION_CLAIM_RE = re.compile(
    r"results reproduce|results were reproduced|verified by execution|runs agree",
    re.IGNORECASE,
)
_PLACEHOLDERS = ("TODO", "_[author: complete]_", "[AUTHOR REVIEW REQUIRED]")


def _load_ledger_records(root: Path, artifact: Path) -> tuple[dict[str, Any], bool]:
    """Merge ledger records from the repository root and beside the artifact.

    Packaged bundles carry their own ledger next to the artifact, so both
    locations are legitimate sources of the record being audited.
    """
    records: dict[str, Any] = {}
    found = False
    for candidate in (root / LEDGER_DIR / LEDGER_NAME, artifact.parent / LEDGER_NAME):
        if not candidate.is_file():
            continue
        found = True
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            for key, value in data.items():
                records.setdefault(key, value)
    return records, found


@app.command("audit-generated")
def audit_generated(
    artifact: Annotated[Path, typer.Argument(help="Generated artifact to audit, e.g. checklist.md.")],
    root: Annotated[Path, typer.Argument(help="Repository root holding .adduce/evidence-ledger.json.")] = Path("."),
) -> None:
    """Audit a generated artifact against its evidence ledger (offline).

    Flags answers without evidence, over-confident yeses, execution claims no
    run backs, leftover placeholders, and post-generation edits."""
    if not artifact.is_file():
        err_console.print(f"[red]error:[/red] {artifact} is not a file")
        raise typer.Exit(code=2)
    records, found = _load_ledger_records(root, artifact)
    if not found:
        err_console.print(
            f"[red]error:[/red] no evidence ledger found at {root / LEDGER_DIR / LEDGER_NAME}. "
            "Generate the artifact with `adduce checklist` or `adduce appendix` so its evidence is recorded."
        )
        raise typer.Exit(code=2)
    keys = [str(artifact)]
    with contextlib.suppress(ValueError):
        keys.append(artifact.resolve().relative_to(root.resolve()).as_posix())
    keys.append(artifact.name)
    record = next((records[k] for k in keys if k in records), None)
    if record is None:  # fall back to a filename match across recorded paths
        record = next((v for k, v in records.items() if Path(k).name == artifact.name), None)
    if record is None:
        err_console.print(
            f"[red]error:[/red] the ledger has no record for {artifact.name}. "
            "Regenerate the artifact so its evidence is recorded."
        )
        raise typer.Exit(code=2)

    rows: list[tuple[str, str, str]] = []
    entries = record.get("entries", [])
    unbacked = [
        e for e in entries
        if e.get("answer") in ("yes", "partial") and not e.get("evidence")
    ]
    if unbacked:
        rows.append((
            "R-GEN-001",
            "fail",
            f"{len(unbacked)} answered item(s) rest on zero evidence items: "
            + ", ".join(str(e.get("item_id", "?")) for e in unbacked[:5]),
        ))
    weak_yes = [
        e for e in entries
        if e.get("answer") == "yes"
        and max((i.get("confidence", 0.0) for i in e.get("evidence", [])), default=0.0) < 0.85
    ]
    if weak_yes:
        rows.append((
            "R-GEN-002",
            "fail",
            "a drafted yes rests on evidence below 0.85 confidence: "
            + ", ".join(str(e.get("item_id", "?")) for e in weak_yes[:5]),
        ))
    text = artifact.read_text(encoding="utf-8", errors="replace")
    has_dynamic = any(
        i.get("strength") == "dynamic_verified" for e in entries for i in e.get("evidence", [])
    )
    claim = _EXECUTION_CLAIM_RE.search(text)
    if claim and not has_dynamic:
        rows.append((
            "R-GEN-003",
            "fail",
            f"the text claims execution ('{claim.group(0)}') but the ledger records no dynamic_verified evidence",
        ))
    placeholders = sum(text.count(marker) for marker in _PLACEHOLDERS)
    if placeholders:
        rows.append((
            "R-GEN-004",
            "info",
            f"{placeholders} unresolved placeholder(s) remain — complete them before submission",
        ))
    recorded_sha = record.get("artifact_sha256", "")
    if recorded_sha and sha256_file(artifact) != recorded_sha:
        rows.append((
            "R-GEN-005",
            "fail",
            "artifact content differs from the ledger record — it was edited after generation; "
            "regenerate, or audit the edits against the evidence",
        ))

    if rows:
        table = Table(box=None, pad_edge=False, header_style="bold dim")
        table.add_column("Rule")
        table.add_column("Level")
        table.add_column("Detail", overflow="fold")
        for rule_id, level, detail in rows:
            style = {"fail": "red", "info": "dim"}.get(level, "")
            table.add_row(rule_id, Text(level, style=style), detail)
        console.print(table)
    else:
        console.print("no generation-safety findings detected.")
    if any(level == "fail" for _, level, _ in rows):
        raise typer.Exit(code=1)


@app.command()
def package(
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
    profile: Annotated[
        str, typer.Option(help=f"Checklist: {', '.join(available_checklists())}, or a path to a checklist YAML.")
    ] = "neurips",
    strict_evidence: Annotated[
        bool,
        typer.Option(
            "--strict-evidence",
            help="Raise the evidence bar: a drafted yes needs stronger detected signals, and inferred-only items go back to the author.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option(help="Overwrite an existing adduce-submission/ directory.")] = False,
) -> None:
    """Assemble a draft submission bundle in adduce-submission/ (offline).

    Checklist, artifact appendix, manifest copy or draft, evidence ledger,
    checksums, citation metadata, and RO-Crate — every file is a draft."""
    try:
        selected = load_checklist(profile)
    except ValueError as exc:
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    result = _run(path)
    package_dir = path / "adduce-submission"
    if package_dir.exists() and not force:
        err_console.print(
            f"[red]error:[/red] {package_dir} already exists; rerun with --force to overwrite it."
        )
        raise typer.Exit(code=2)
    package_dir.mkdir(parents=True, exist_ok=True)

    def write_file(name: str, content: str) -> None:
        (package_dir / name).write_text(content.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        written.append(name)

    written: list[str] = []
    checklist_md, checklist_ledger = render_markdown(selected, result, strict=strict_evidence)
    checklist_ledger.artifact_path = "checklist.md"
    checklist_ledger.provenance["command"] = "package"
    write_file("checklist.md", checklist_md)

    appendix_md, appendix_ledger = appendix_report.render(result, strict=strict_evidence)
    appendix_ledger.artifact_path = "artifact_appendix.md"
    appendix_ledger.provenance["command"] = "package"
    write_file("artifact_appendix.md", appendix_md)

    # The manifest goes into the package only: this command never touches
    # .adduce/, so a scaffolded draft cannot masquerade as author-confirmed.
    existing_manifest = path / ".adduce" / "manifest.yaml"
    if existing_manifest.is_file():
        manifest_text = existing_manifest.read_text(encoding="utf-8")
    else:
        draft = scaffold_manifest(result.evidence)
        manifest_text = yaml.safe_dump(draft.to_dict(), sort_keys=False, allow_unicode=True)
    write_file("manifest.yaml", manifest_text)

    ledgers: dict[str, Ledger] = {
        checklist_ledger.artifact_path: checklist_ledger,
        appendix_ledger.artifact_path: appendix_ledger,
    }
    write_file(
        "evidence-ledger.json",
        json.dumps({key: ledger.to_dict() for key, ledger in ledgers.items()}, indent=2),
    )
    write_file("checksums.txt", checksums_report.render(result))
    for candidate in ("CITATION.cff", "citation.cff"):
        source = path / candidate
        if source.is_file():
            write_file("citation.cff", source.read_text(encoding="utf-8"))
            break
    write_file("ro-crate-metadata.json", ro_crate_report.render(result))

    combined = checklist_ledger.counts()
    for key, value in appendix_ledger.counts().items():
        combined[key] += value
    _print_generation_summary(combined, package_dir / "evidence-ledger.json")
    for name in written:
        console.print(f"written: {package_dir / name}")
    console.print(
        "Every file is a draft; run `adduce audit-generated adduce-submission/checklist.md` before submitting."
    )


_EXPORTERS = {
    "ro-crate": ("ro-crate-metadata.json", ro_crate_report.render),
    "codemeta": ("codemeta.json", codemeta_report.render),
    "zenodo": (".zenodo.json", zenodo_report.render),
    "checksums": ("checksums.txt", checksums_report.render),
    "software-heritage": ("SOFTWARE_HERITAGE.md", swh_report.render),
}


@app.command()
def export(
    what: Annotated[str, typer.Argument(help=f"One of: {', '.join([*_EXPORTERS, 'croissant', 'all'])}.")],
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
) -> None:
    """Write archival metadata bundles (RO-Crate, Croissant, CodeMeta, Zenodo, checksums) — offline."""
    valid = {*_EXPORTERS, "croissant", "all"}
    if what not in valid:
        err_console.print(f"[red]error:[/red] unknown export '{what}'. Choose from: {', '.join(sorted(valid))}.")
        raise typer.Exit(code=2)
    result = _run(path)
    selected = list(_EXPORTERS.items()) if what == "all" else ([(what, _EXPORTERS[what])] if what in _EXPORTERS else [])
    for _, (filename, renderer) in selected:
        target = path / filename
        if target.exists() and not force:
            console.print(f"skipped (exists): {target}")
            continue
        target.write_text(renderer(result).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
        console.print(f"written: {target}")
    if what in ("croissant", "all"):
        documents = json.loads(croissant_report.render(result))
        if not documents:
            console.print("croissant: no datasets detected or declared; add them to the manifest first.")
        for dataset_id, document in documents.items():
            safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in dataset_id)
            target = path / f"{safe}.croissant.json"
            if target.exists() and not force:
                console.print(f"skipped (exists): {target}")
                continue
            target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
            console.print(f"written: {target}")
    console.print("every export is a draft: fill the marked fields before depositing.")


# --------------------------------------------------------------------------
# badge / baseline / diff / archive-plan
# --------------------------------------------------------------------------


@app.command()
def badge(
    path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path("."),
    svg: Annotated[bool, typer.Option(help="Emit a self-contained SVG instead of shields.io endpoint JSON.")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write to a file instead of stdout.")] = None,
) -> None:
    """Emit the reproducibility badge (endpoint JSON or SVG) — offline, no hosted endpoint."""
    result = _run(path)
    rendered = badge_report.render_svg(result) if svg else badge_report.render(result)
    _write_or_print(rendered, output)


@app.command()
def baseline(
    path: Annotated[Path, typer.Argument(help="Repository root to snapshot.")] = Path("."),
    profile: Annotated[str | None, typer.Option(help="Scoring profile to snapshot under.")] = None,
) -> None:
    """Record the current state so CI can fail only on regressions, not pre-existing debt."""
    result = _run(path, profile)
    snapshot = baseline_snapshot(result.card)
    target = path / BASELINE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8", newline="\n")
    console.print(
        f"baseline written to {target} (score {result.card.total:.0f}/100, {len(snapshot['rules'])} rules recorded)"
    )
    console.print("commit this file, then gate CI with: adduce check --fail-on-regression")


@app.command("diff")
def artifact_diff(
    revision_range: Annotated[str, typer.Argument(help="Git revision range, e.g. main...HEAD or HEAD~3..HEAD.")],
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
) -> None:
    """Artifact regression mode: flag code/result changes not reflected in docs, configs, or the manifest."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "diff", "--name-only", revision_range],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        err_console.print(f"[red]error:[/red] git diff failed: {exc}")
        raise typer.Exit(code=2) from exc
    if completed.returncode != 0:
        err_console.print(f"[red]error:[/red] {completed.stderr.strip()}")
        raise typer.Exit(code=2)
    changed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not changed:
        console.print(f"no changes in {revision_range}.")
        raise typer.Exit()

    def classify(file: str) -> str:
        lowered = file.lower()
        if lowered.startswith(".adduce/") or lowered.endswith(("manifest.yaml", "manifest.json")):
            return "manifest"
        if lowered.endswith((".md", ".rst", ".tex", ".bib")) or "readme" in lowered or "citation" in lowered:
            return "docs"
        if any(part in lowered for part in ("results/", "outputs/", "metrics/")) or lowered.endswith((".csv", ".jsonl")):
            return "results"
        if lowered.endswith((".yaml", ".yml", ".json", ".toml", ".cfg", ".gin")):
            return "configs"
        if lowered.endswith((".py", ".sh", ".slurm", ".ipynb")) or "makefile" in lowered or "dockerfile" in lowered:
            return "code"
        return "other"

    groups: dict[str, list[str]] = {}
    for file in changed:
        groups.setdefault(classify(file), []).append(file)

    for group in ("code", "configs", "results", "docs", "manifest", "other"):
        files = groups.get(group, [])
        if files:
            console.print(f"[bold]{group}[/bold] ({len(files)})")
            for file in files[:8]:
                console.print(f"  {file}")
            if len(files) > 8:
                console.print(Text(f"  … and {len(files) - 8} more", style="dim"))

    substantive = bool(groups.get("code") or groups.get("configs") or groups.get("results"))
    reflected = bool(groups.get("docs") or groups.get("manifest"))
    console.print()
    if substantive and not reflected:
        console.print(
            "[yellow]code, configs, or results changed but neither the docs nor the manifest did — "
            "reported numbers, the checklist, and the manifest may now be stale.[/yellow]"
        )
        console.print("refresh with: adduce manifest && adduce checklist --profile <venue>")
        raise typer.Exit(code=1)
    console.print("[green]changes are reflected in docs/manifest (or nothing substantive changed).[/green]")


@app.command("archive-plan")
def archive_plan(path: Annotated[Path, typer.Argument(help="Repository root to scan.")] = Path(".")) -> None:
    """The exact steps to obtain a persistent DOI / SWHID for this repository. Nothing is uploaded for you."""
    result = _run(path)
    ev = result.evidence
    console.print("[bold]Archival plan[/bold]\n")
    step = 1

    def print_step(text: str, done: bool = False) -> None:
        nonlocal step
        marker = "[green](done)[/green] " if done else ""
        console.print(f"  {step}. {marker}{text}")
        step += 1

    print_step("Make the repository public on GitHub/GitLab.", done=bool(result.repo.git.remotes))
    oversized = ev.data.untracked_binaries
    if oversized:
        print_step(
            f"Move {len(oversized)} large binary file(s) out of git (see R-DATA-004/R-ARC-002) — "
            "archives reject or bloat on committed blobs."
        )
    print_step("Generate deposit metadata: adduce export zenodo && adduce export codemeta.", done=ev.repo.exists(".zenodo.json") and ev.repo.exists("codemeta.json"))
    print_step("Tag the exact state behind the paper: git tag v1.0-paper && git push --tags.", done=bool(result.repo.git.tags))
    print_step("Enable the Zenodo-GitHub integration (zenodo.org → GitHub) for the repository.")
    print_step("Create a GitHub release for the tag; Zenodo archives it and mints a DOI automatically.")
    print_step("Put the concept DOI in the README and CITATION.cff.", done=ev.git.has_archival_doi)
    print_step("Optionally, trigger Software Heritage archival: adduce export software-heritage for the steps.")
    console.print("\nadduce prepares metadata and instructions; the deposits themselves happen in your browser.")


# --------------------------------------------------------------------------
# online + dynamic (fenced)
# --------------------------------------------------------------------------


def _resolve_and_print(
    result: CheckResult, output_console: Console = console
) -> list[tuple[str, str | None]]:
    """Resolve detected remote references; returns (identifier, sha) pairs."""
    from .cache import Cache
    from .dynamic import resolve

    cache = Cache(result.repo.root)
    resolved: list[tuple[str, str | None]] = []
    output_console.print(
        "[bold]Online resolution[/bold] (public metadata, from this machine, cached in .adduce/cache)"
    )
    seen: set[str] = set()
    for ref in result.evidence.remote.references:
        if ref.kind in {"hf", "sentence_transformers"}:
            identifier = ref.spec.split('"')[1] if '"' in ref.spec else None
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            is_dataset = "load_dataset" in ref.spec
            outcome = resolve.resolve_hf(identifier, cache, dataset=is_dataset)
            status = f"[green]{outcome.sha[:12]}[/green]" if outcome.sha else f"[red]{outcome.detail}[/red]"
            output_console.print(f"  {identifier}: {status}")
            resolved.append((identifier, outcome.sha))
        elif ref.kind == "url" and ref.spec.startswith("http") and ref.spec not in seen:
            seen.add(ref.spec)
            outcome = resolve.resolve_url(ref.spec, cache)
            color = "green" if outcome.ok else "red"
            output_console.print(f"  {ref.spec[:70]}: [{color}]{outcome.detail}[/{color}]")
    if not seen:
        output_console.print(Text("  no resolvable remote references detected", style="dim"))
    return resolved


@app.command("pin-remotes")
def pin_remotes(
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
    diff: Annotated[bool, typer.Option("--diff", help="Show the revision-pinning edits as a diff.")] = False,
    write: Annotated[bool, typer.Option("--write", help="Apply the edits after showing the diff.")] = False,
) -> None:
    """Detect floating remote references; optionally resolve current SHAs (online) and pin them.

    Pinning to the current SHA is a forward guarantee — it does not recover
    the version originally used. Verify before trusting."""
    result = _run(path)
    refs = result.evidence.remote.references
    unpinned = [r for r in refs if not r.pinned and r.kind in {"hf", "sentence_transformers"}]
    console.print(f"{len(refs)} remote reference(s); {len(unpinned)} pinnable Hugging Face call(s) without an immutable revision.")
    for ref in unpinned:
        console.print(f"  {ref.file}:{ref.line}  {ref.spec}")
    if not (diff or write):
        console.print("\nresolve and draft the pins with: adduce pin-remotes --diff   (opt-in online step)")
        raise typer.Exit()

    resolved = dict(_resolve_and_print(result))
    revisions = {identifier: sha for identifier, sha in resolved.items() if sha}
    if not revisions:
        console.print("nothing resolvable to pin.")
        raise typer.Exit()

    from .fixers.codemods.pin_revision import pin_revisions, unified_diff

    total_changes = 0
    for file in sorted({r.file for r in unpinned}):
        source = result.repo.read_text(file)
        if source is None:
            continue
        try:
            new_source, changes = pin_revisions(source, revisions)
        except Exception as exc:  # libcst parse failure on unusual syntax
            err_console.print(f"[yellow]skipped {file}:[/yellow] {exc}")
            continue
        if changes == 0:
            continue
        total_changes += changes
        console.print(unified_diff(file, source, new_source))
        if write:
            (path / file).write_text(new_source, encoding="utf-8", newline="\n")
            console.print(f"[green]applied {changes} pin(s) to {file}[/green]")
    if total_changes and not write:
        console.print("apply with: adduce pin-remotes --write")
    if total_changes:
        console.print(
            "[yellow]these pins record the CURRENT upstream revision, which may differ from the version "
            "originally used for the paper — verify against your results before trusting them.[/yellow]"
        )


@app.command()
def reproduce(
    path: Annotated[Path, typer.Argument(help="Repository root.")] = Path("."),
    command: Annotated[str | None, typer.Option(help="Command to run twice (defaults to the manifest smoke target).")] = None,
    expected_output: Annotated[
        list[str] | None,
        typer.Option(
            "--expected-output",
            help="Relative output file that both runs must produce identically (repeatable).",
        ),
    ] = None,
    expected_metric: Annotated[
        list[str] | None,
        typer.Option(
            "--expected-metric",
            help="Named stdout metric that both runs must report identically (repeatable).",
        ),
    ] = None,
    seed: Annotated[int, typer.Option(help="Seed exported as PYTHONHASHSEED/ADDUCE_SEED for both runs.")] = 0,
    timeout_minutes: Annotated[int, typer.Option(help="Per-run timeout.")] = 30,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm executing repository code.")] = False,
) -> None:
    """Run the smoke target twice and check the runs agree (EXECUTES REPOSITORY CODE; opt-in).

    Designed to run inside the repository's own container or CI where the
    environment already exists. Never invoked by `adduce check`."""
    result = _run(path)
    smoke = result.evidence.manifest.smoke
    chosen = command or smoke.command
    if not chosen:
        err_console.print(
            "[red]error:[/red] no command given and no smoke target in the manifest. "
            "Add a [smoke] block via `adduce manifest` or pass --command."
        )
        raise typer.Exit(code=2)
    if command:
        expected_outputs = expected_output or []
        expected_metrics = expected_metric or []
    else:
        expected_outputs = expected_output if expected_output is not None else smoke.expected_outputs
        expected_metrics = expected_metric if expected_metric is not None else smoke.expected_metrics
    if not expected_outputs and not expected_metrics:
        err_console.print(
            "[red]error:[/red] no comparable fingerprint configured. Add smoke.expected_outputs "
            "or smoke.expected_metrics to the manifest, or pass --expected-output/--expected-metric."
        )
        raise typer.Exit(code=2)
    if not yes:
        err_console.print(
            f"about to execute repository code twice: `{chosen}`\n"
            "this inherits the repository's full environment and risk. Re-run with --yes to proceed."
        )
        raise typer.Exit(code=2)

    from .dynamic.reproduce import reproduce as run_reproduce
    from .dynamic.reproduce import save_report

    console.print(f"run 1 and 2 of: {chosen}  (seed {seed}, timeout {timeout_minutes} min/run)")
    report = run_reproduce(
        path,
        chosen,
        expected_outputs,
        seed=seed,
        timeout_minutes=timeout_minutes,
        expected_metrics=expected_metrics,
    )
    target = save_report(path, report)
    if report.agree:
        console.print(
            f"[green]runs agree[/green]: {len(report.comparable_fingerprints)} "
            "expected fingerprint(s) matched."
        )
    else:
        console.print("[red]runs disagree:[/red]")
        for line in report.disagreements:
            console.print(f"  - {line}")
    console.print(f"full report: {target}")
    raise typer.Exit(code=0 if report.agree else 1)


# --------------------------------------------------------------------------
# fix / rules / explain
# --------------------------------------------------------------------------


@app.command()
def fix(
    path: Annotated[Path, typer.Argument(help="Repository root to scaffold into.")] = Path("."),
    scaffold: Annotated[str | None, typer.Option(help=f"Scaffold to generate: {', '.join(SCAFFOLDS)}.")] = None,
    rule: Annotated[str | None, typer.Option(help="Generate the scaffold that addresses this rule ID.")] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Deprecated compatibility option; existing files are still never overwritten.",
        ),
    ] = False,
    list_scaffolds: Annotated[bool, typer.Option("--list", help="List available scaffolds and exit.")] = False,
) -> None:
    """Generate the files the checks ask for (non-destructive; existing files are skipped)."""
    if list_scaffolds:
        for key, (_, description) in SCAFFOLDS.items():
            console.print(f"  [bold]{key:<10}[/bold] {description}")
        raise typer.Exit()
    if force:
        err_console.print(
            "[yellow]warning:[/yellow] --force is deprecated and does not overwrite existing files."
        )
    if rule:
        scaffold = RULE_TO_SCAFFOLD.get(rule.upper())
        if scaffold is None:
            err_console.print(
                f"[red]error:[/red] no scaffold addresses {rule}. Rules with scaffolds: {', '.join(sorted(RULE_TO_SCAFFOLD))}."
            )
            raise typer.Exit(code=2)
    if scaffold is None:
        err_console.print("[red]error:[/red] pass --scaffold <name> or --rule <rule-id>; see --list.")
        raise typer.Exit(code=2)
    if scaffold not in SCAFFOLDS:
        err_console.print(f"[red]error:[/red] unknown scaffold '{scaffold}'. Available: {', '.join(SCAFFOLDS)}.")
        raise typer.Exit(code=2)
    result = _run(path)
    scaffold_fn, _ = SCAFFOLDS[scaffold]
    outcome = scaffold_fn(result)
    console.print(f"{outcome.action}: {outcome.path}")
    if outcome.action != "skipped (exists)":
        console.print("review every [AUTHOR REVIEW REQUIRED] marker before committing.")


@app.command()
def rules(
    category: Annotated[str | None, typer.Option(help="Filter by category substring, e.g. 'determinism'.")] = None,
) -> None:
    """List all registered rules (built-in and plugins)."""
    table = Table(box=None, header_style="bold dim")
    table.add_column("ID")
    table.add_column("Category")
    table.add_column("Severity")
    table.add_column("Weight", justify="right")
    table.add_column("Title")
    for rule_obj in discover_rules():
        if category and category.lower() not in rule_obj.category.value.lower():
            continue
        table.add_row(
            rule_obj.id,
            rule_obj.category.value,
            rule_obj.effective_severity,
            str(rule_obj.weight),
            rule_obj.title,
        )
    console.print(table)


@app.command()
def explain(rule_id: Annotated[str, typer.Argument(help="Rule ID, e.g. R-DET-001.")]) -> None:
    """Explain what a rule checks, why it matters, and how to satisfy it."""
    for rule_obj in discover_rules():
        if rule_obj.id == rule_id.upper():
            console.print(f"[bold]{rule_obj.id}[/bold] — {rule_obj.title}")
            console.print(f"category: {rule_obj.category.value}   weight: {rule_obj.weight}")
            console.print()
            console.print(rule_obj.rationale)
            if rule_obj.fix_command:
                console.print()
                console.print(f"scaffold available: [cyan]{rule_obj.fix_command}[/cyan]")
            console.print()
            console.print(Text(f"suppress inline with:  # adduce: ignore={rule_obj.id}"))
            raise typer.Exit()
    err_console.print(f"[red]error:[/red] unknown rule '{rule_id}'. See `adduce rules`.")
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
