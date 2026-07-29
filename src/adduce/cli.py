"""Command-line interface.

One simple entrypoint (``adduce check``) runs everything safe and local;
focused subcommands add depth. Anything online or executing repository code
is a separate, opt-in command and says so.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, NoReturn

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
    EVIDENCE_ONLY_TEXT_POLICY,
    LEDGER_DIR,
    LEDGER_NAME,
    PROVIDER_UNVERIFIED_TEXT_POLICY,
    Ledger,
    sha256_text,
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
from .safe_write import (
    RegularTextSnapshot,
    SafeWriteError,
    create_text_exclusive,
    ensure_safe_directory,
    ensure_safe_directory_tree,
    read_text_regular,
    regular_file_exists,
    replace_text_regular,
    replace_text_regular_if_unchanged,
    snapshot_text_regular,
)

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
    online: bool = False,
    honor_repository_policy: bool = True,
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
            online=online,
            honor_repository_policy=honor_repository_policy,
        )
    except ValueError as exc:  # unknown profile, malformed config
        err_console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _write_or_print(rendered: str, output: Path | None) -> None:
    text = rendered.rstrip("\n") + "\n"
    if output:
        try:
            ensure_safe_directory_tree(output.parent, label="output directory")
            replace_text_regular(
                output,
                text,
                label="output artifact",
                parent_label="output directory",
            )
        except SafeWriteError as exc:
            _generation_write_error(exc)
        err_console.print(f"written to {output}")
    else:
        sys.stdout.write(text)


def _preflight_output(output: Path | None) -> None:
    """Reject an unsafe artifact destination before its ledger is updated."""
    if output is None:
        return
    try:
        ensure_safe_directory_tree(output.parent, label="output directory")
        regular_file_exists(output, label="output artifact")
    except SafeWriteError as exc:
        _generation_write_error(exc)


def _generation_write_error(exc: SafeWriteError) -> NoReturn:
    """Turn a bounded generation-write refusal into a stable CLI failure."""
    err_console.print(Text.assemble(("error: ", "red"), str(exc)))
    raise typer.Exit(code=2) from exc


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


def _print_generation_summary(
    counts: dict[str, int],
    ledger_path: Path,
    *,
    unverified_provider_fragments: int = 0,
) -> None:
    """Summarise what the draft rests on — to stderr, never into the artifact."""
    err_console.print(
        "generation summary: "
        f"{counts['evidence_backed']} evidence-backed, {counts['partial']} partial, "
        f"{counts['author_input_required']} author input required, "
        f"{counts['not_detected']} not detected, {counts['unknown']} unknown, "
        f"{counts['conflicts']} conflict(s)"
    )
    if unverified_provider_fragments:
        err_console.print(
            f"{unverified_provider_fragments} unverified provider-generated prose "
            "fragment(s) require author review"
        )
    err_console.print(f"ledger: {ledger_path}")
    if (
        counts["partial"]
        or counts["author_input_required"]
        or counts["conflicts"]
        or unverified_provider_fragments
    ):
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
    mode: Annotated[
        Mode,
        typer.Option(
            help=(
                "Report policy: author applies repository Adduce configuration; "
                "reviewer and ae-chair use neutral defaults unless CLI options are explicit."
            )
        ),
    ] = Mode.AUTHOR,
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
    """Scan a repository offline unless the explicit --online option is selected."""
    if output_format not in _FORMATS:
        err_console.print(f"[red]error:[/red] unknown format '{output_format}'. Choose from: {', '.join(_FORMATS)}.")
        raise typer.Exit(code=2)
    if fail_under is not None and (
        not math.isfinite(fail_under) or not 0 <= fail_under <= 100
    ):
        err_console.print("[red]error:[/red] --fail-under must be a finite number from 0 to 100")
        raise typer.Exit(code=2)

    result = _run(
        path,
        profile,
        ignore,
        exclude,
        only,
        skip,
        paper=paper,
        online=online,
        honor_repository_policy=mode is Mode.AUTHOR,
    )

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
        try:
            baseline_source = read_text_regular(
                baseline_path,
                label="baseline",
                parent_label=".adduce directory",
            )
        except (SafeWriteError, UnicodeError) as exc:
            _generation_write_error(
                exc
                if isinstance(exc, SafeWriteError)
                else SafeWriteError("baseline is not valid UTF-8")
            )
        if baseline_source is None:
            err_console.print(
                f"[yellow]no {BASELINE_FILENAME} found; run `adduce baseline` first. Not failing.[/yellow]"
            )
        else:
            def reject_non_finite_baseline(value: str) -> None:
                raise ValueError(f"non-finite JSON value {value}")

            try:
                baseline_value = json.loads(
                    baseline_source,
                    parse_constant=reject_non_finite_baseline,
                )
            except (json.JSONDecodeError, ValueError):
                _generation_write_error(SafeWriteError("invalid baseline JSON"))
            if not isinstance(baseline_value, dict):
                _generation_write_error(SafeWriteError("invalid baseline: expected an object"))
            try:
                regressed = regressions_against(result.card, baseline_value)
            except ValueError as exc:
                _generation_write_error(SafeWriteError(str(exc)))
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
    try:
        if result.evidence.manifest.exists:
            target = write_manifest_proposal(result.repo.root, draft)
            console.print(
                f"manifest refresh proposal written to {target}; "
                "the existing manifest was unchanged"
            )
        else:
            target = write_manifest(result.repo.root, draft)
            console.print(f"manifest written to {target}")
    except SafeWriteError as exc:
        _generation_write_error(exc)
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
        typer.Option(
            help=(
                "Request unverified free-text drafts from your configured LLM "
                "(BYO-key; prose never counts as evidence)."
            )
        ),
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
    llm_provider: tuple[str, str] | None = None
    if llm:
        from . import llm as llm_module

        try:
            identity = llm_module.provider_identity()
        except llm_module.LLMUnavailable as exc:
            err_console.print(f"[yellow]LLM drafting skipped:[/yellow] {exc}")
        else:
            llm_provider = (identity.provider, identity.model)
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
                    llm_drafts[item.id] = llm_module.draft_justification(
                        item.question, evidence_lines
                    )
                except llm_module.LLMUnavailable as exc:
                    err_console.print(f"[yellow]LLM drafting skipped:[/yellow] {exc}")
                    break
    rendered, ledger = render_markdown(
        selected,
        result,
        llm_drafts=llm_drafts,
        llm_provider=llm_provider,
        strict=strict_evidence,
    )
    ledger.artifact_path = _ledger_key(output, path, ledger.artifact_path)
    _preflight_output(output)
    try:
        ledger_path = write_ledger(result.repo.root, ledger)
    except SafeWriteError as exc:
        _generation_write_error(exc)
    _write_or_print(rendered, output)
    _print_generation_summary(
        ledger.counts(),
        ledger_path,
        unverified_provider_fragments=len(ledger.generated_text_provenance),
    )


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
    ledger.artifact_path = _ledger_key(output, path, ledger.artifact_path)
    _preflight_output(output)
    try:
        ledger_path = write_ledger(result.repo.root, ledger)
    except SafeWriteError as exc:
        _generation_write_error(exc)
    _write_or_print(rendered, output)
    _print_generation_summary(ledger.counts(), ledger_path)


# --------------------------------------------------------------------------
# generation safety: audit-generated / package
# --------------------------------------------------------------------------

_EXECUTION_CLAIM_RE = re.compile(
    r"\bresults?\s+(?:were|was|have been|has been)\s+"
    r"(?:reproduced|replicated|rerun|re-run|executed)\b"
    r"|\b(?:i|we|adduce)\s+(?:have\s+)?(?:successfully\s+)?"
    r"(?:ran|reran|executed|reproduced|replicated)\b"
    r"|\b(?:all\s+)?(?:experiments?|runs?)\s+(?:were|have been)\s+"
    r"(?:executed|run|rerun|re-run|reproduced|replicated)\b"
    r"|\b(?:verified|validated)\s+by\s+execution\b"
    r"|\bruns?\s+agree(?:d|s)?\b"
    r"|\bmatched\s+(?:all\s+)?(?:the\s+)?(?:reported\s+)?"
    r"(?:results?|numbers?|metrics?)\b"
    r"|\b(?:the\s+)?(?:re-?run|reproduction)\s+matched\s+"
    r"(?:the\s+)?(?:paper|publication|report|figure|table|result|metric|number)s?\b"
    r"|\b(?:training|evaluation|experiment|pipeline|workflow|script|command)\s+"
    r"(?:completed|ran|executed)\s+successfully\b"
    r"|\breproduced\s+(?:figure|table|result|metric|number)s?\s*(?:[A-Z]?\d+)?\b",
    re.IGNORECASE,
)
_PROVIDER_FRAGMENT_MARKER_RE = re.compile(
    r'^<!-- adduce-provider-fragment id-sha256="([0-9a-f]{64})" '
    r'text-sha256="([0-9a-f]{64})" -->$',
    re.MULTILINE,
)
_PLACEHOLDERS = ("TODO", "_[author: complete]_", "[AUTHOR REVIEW REQUIRED]")


def _load_ledger_records(root: Path, artifact: Path) -> tuple[dict[str, Any], bool]:
    """Merge ledger records from the repository root and beside the artifact.

    Packaged bundles carry their own ledger next to the artifact, so both
    locations are legitimate sources of the record being audited.
    """
    records: dict[str, Any] = {}
    found = False

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value}")

    for candidate in (root / LEDGER_DIR / LEDGER_NAME, artifact.parent / LEDGER_NAME):
        snapshot = snapshot_text_regular(
            candidate,
            label="evidence ledger",
            parent_label="evidence ledger directory",
        )
        if snapshot is None:
            continue
        found = True
        try:
            data = json.loads(
                snapshot.text,
                parse_constant=reject_non_finite,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise SafeWriteError("invalid evidence ledger") from exc
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in data.items()
        ):
            raise SafeWriteError("invalid evidence ledger")
        for key, value in data.items():
            records.setdefault(key, value)
    return records, found


def _validated_ledger_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SafeWriteError("invalid evidence ledger record")
    required_fields = {
        "artifact_path",
        "artifact_sha256",
        "provenance",
        "generated_text_policy",
        "counts",
        "entries",
    }
    allowed_fields = required_fields | {"generated_text_provenance"}
    if not required_fields.issubset(value) or not set(value).issubset(allowed_fields):
        raise SafeWriteError("invalid evidence ledger record")
    if not isinstance(value["artifact_path"], str) or not value["artifact_path"]:
        raise SafeWriteError("invalid evidence ledger record")
    recorded_sha = value["artifact_sha256"]
    if not isinstance(recorded_sha, str) or not re.fullmatch(
        r"[0-9a-f]{64}", recorded_sha
    ):
        raise SafeWriteError("invalid evidence ledger record")
    provenance = value["provenance"]
    provenance_fields = {
        "adduce_version",
        "command",
        "profile",
        "mode",
        "repo_commit",
        "generated_at",
    }
    if not isinstance(provenance, dict) or set(provenance) != provenance_fields:
        raise SafeWriteError("invalid evidence ledger record")
    if any(
        not isinstance(provenance.get(field), str) or not provenance[field]
        for field in ("adduce_version", "command", "mode", "generated_at")
    ):
        raise SafeWriteError("invalid evidence ledger record")
    if provenance["mode"] not in {"default", "strict"}:
        raise SafeWriteError("invalid evidence ledger record")
    if provenance["profile"] is not None and not isinstance(
        provenance["profile"], str
    ):
        raise SafeWriteError("invalid evidence ledger record")
    if provenance["repo_commit"] is not None and not isinstance(
        provenance["repo_commit"], str
    ):
        raise SafeWriteError("invalid evidence ledger record")

    entries = value["entries"]
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        raise SafeWriteError("invalid evidence ledger record")
    valid_answers = {
        "yes",
        "partial",
        "not_detected",
        "author_input_required",
        "unknown",
    }
    valid_strengths = {
        "direct",
        "inferred",
        "manifest_author_confirmed",
    }
    entry_fields = {
        "item_id",
        "question",
        "answer",
        "evidence",
        "searched",
        "missing",
        "conflicts",
    }
    observed_item_ids: set[str] = set()
    for entry in entries:
        if set(entry) != entry_fields:
            raise SafeWriteError("invalid evidence ledger record")
        item_id = entry["item_id"]
        if (
            not isinstance(item_id, str)
            or not item_id
            or item_id in observed_item_ids
            or not isinstance(entry["question"], str)
            or not entry["question"]
        ):
            raise SafeWriteError("invalid evidence ledger record")
        observed_item_ids.add(item_id)
        answer = entry.get("answer")
        if answer not in valid_answers:
            raise SafeWriteError("invalid evidence ledger record")
        for field in ("searched", "missing", "conflicts"):
            values = entry[field]
            if not isinstance(values, list) or any(
                not isinstance(item, str) or not item for item in values
            ):
                raise SafeWriteError("invalid evidence ledger record")
        if answer == "not_detected" and (
            not entry["searched"] or not entry["missing"]
        ):
            raise SafeWriteError("invalid evidence ledger record")
        evidence = entry["evidence"]
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise SafeWriteError("invalid evidence ledger record")
        for item in evidence:
            if set(item) != {"kind", "path", "line", "confidence", "strength"}:
                raise SafeWriteError("invalid evidence ledger record")
            if (
                not isinstance(item["kind"], str)
                or not item["kind"]
                or not isinstance(item["path"], str)
                or (not item["path"] and item["line"] is not None)
                or (
                    item["line"] is not None
                    and (
                        isinstance(item["line"], bool)
                        or not isinstance(item["line"], int)
                        or item["line"] < 1
                    )
                )
            ):
                raise SafeWriteError("invalid evidence ledger record")
            confidence = item["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(float(confidence))
                or not 0.0 <= float(confidence) <= 1.0
            ):
                raise SafeWriteError("invalid evidence ledger record")
            if item["strength"] not in valid_strengths:
                raise SafeWriteError("invalid evidence ledger record")
            if item["strength"] == "manifest_author_confirmed":
                if (
                    item["kind"] != "manifest"
                    or item["path"] != f"{LEDGER_DIR}/manifest.yaml"
                    or item["line"] is not None
                    or float(item["confidence"]) != 1.0
                    or "manifest" not in entry["searched"]
                ):
                    raise SafeWriteError("invalid evidence ledger record")
            elif item["kind"] == "manifest" or (
                item["strength"] == "direct" and not item["path"]
            ):
                raise SafeWriteError("invalid evidence ledger record")
            if item["kind"] != "manifest" and item["kind"] not in entry["searched"]:
                raise SafeWriteError("invalid evidence ledger record")
        if len(entry["searched"]) != len(set(entry["searched"])):
            raise SafeWriteError("invalid evidence ledger record")

    counts = value["counts"]
    count_keys = {
        "evidence_backed",
        "partial",
        "author_input_required",
        "not_detected",
        "unknown",
        "conflicts",
    }
    if (
        not isinstance(counts, dict)
        or set(counts) != count_keys
        or any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            for count in counts.values()
        )
    ):
        raise SafeWriteError("invalid evidence ledger record")
    expected_counts = {
        "evidence_backed": sum(entry["answer"] == "yes" for entry in entries),
        "partial": sum(entry["answer"] == "partial" for entry in entries),
        "author_input_required": sum(
            entry["answer"] == "author_input_required" for entry in entries
        ),
        "not_detected": sum(
            entry["answer"] == "not_detected" for entry in entries
        ),
        "unknown": sum(entry["answer"] == "unknown" for entry in entries),
        "conflicts": sum(bool(entry["conflicts"]) for entry in entries),
    }
    if counts != expected_counts:
        raise SafeWriteError("invalid evidence ledger record")

    text_policy = value["generated_text_policy"]
    if text_policy not in {
        EVIDENCE_ONLY_TEXT_POLICY,
        PROVIDER_UNVERIFIED_TEXT_POLICY,
    }:
        raise SafeWriteError("invalid evidence ledger record")
    text_provenance = value.get("generated_text_provenance", [])
    if not isinstance(text_provenance, list) or any(
        not isinstance(item, dict) for item in text_provenance
    ):
        raise SafeWriteError("invalid evidence ledger record")
    item_ids = observed_item_ids
    seen_fragment_ids: set[str] = set()
    required_fragment_fields = {
        "item_id",
        "source",
        "provider",
        "model",
        "text_sha256",
        "author_review_required",
    }
    for fragment in text_provenance:
        if set(fragment) != required_fragment_fields:
            raise SafeWriteError("invalid evidence ledger record")
        fragment_id = fragment.get("item_id")
        if (
            not isinstance(fragment_id, str)
            or not fragment_id
            or fragment_id not in item_ids
            or fragment_id in seen_fragment_ids
        ):
            raise SafeWriteError("invalid evidence ledger record")
        seen_fragment_ids.add(fragment_id)
        if fragment.get("source") != "external_model":
            raise SafeWriteError("invalid evidence ledger record")
        if any(
            not isinstance(fragment.get(field), str) or not fragment[field]
            for field in ("provider", "model")
        ):
            raise SafeWriteError("invalid evidence ledger record")
        text_sha256 = fragment.get("text_sha256")
        if not isinstance(text_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", text_sha256
        ):
            raise SafeWriteError("invalid evidence ledger record")
        if fragment.get("author_review_required") is not True:
            raise SafeWriteError("invalid evidence ledger record")
    if (
        text_policy == EVIDENCE_ONLY_TEXT_POLICY
        and text_provenance
    ) or (
        text_policy == PROVIDER_UNVERIFIED_TEXT_POLICY
        and not text_provenance
    ):
        raise SafeWriteError("invalid evidence ledger record")
    return value


def _yes_has_strong_evidence(
    entry: dict[str, Any],
    *,
    strict: bool,
) -> bool:
    """Re-check the affirmative-answer policy from serialized evidence."""
    evidence = entry["evidence"]
    if not evidence or entry["conflicts"]:
        return False
    threshold = 0.90 if strict else 0.85
    if any(
        item["strength"] == "manifest_author_confirmed"
        and item["kind"] == "manifest"
        and item["path"] == f"{LEDGER_DIR}/manifest.yaml"
        and float(item["confidence"]) == 1.0
        for item in evidence
    ):
        return True
    return all(
        item["strength"] == "direct"
        and bool(item["path"])
        and float(item["confidence"]) >= threshold
        for item in evidence
    )


@app.command("audit-generated")
def audit_generated(
    artifact: Annotated[Path, typer.Argument(help="Generated artifact to audit, e.g. checklist.md.")],
    root: Annotated[Path, typer.Argument(help="Repository root holding .adduce/evidence-ledger.json.")] = Path("."),
) -> None:
    """Audit a generated artifact against its evidence ledger (offline).

    Flags answers without evidence, over-confident yeses, execution claims no
    run backs, unverified provider prose, leftover placeholders, and
    post-generation edits."""
    try:
        artifact_snapshot = snapshot_text_regular(
            artifact,
            label="generated artifact",
            parent_label="artifact directory",
        )
    except SafeWriteError as exc:
        _generation_write_error(exc)
    if artifact_snapshot is None:
        err_console.print(f"[red]error:[/red] {artifact} is not a file")
        raise typer.Exit(code=2)
    try:
        records, found = _load_ledger_records(root, artifact)
    except SafeWriteError as exc:
        _generation_write_error(exc)
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
    try:
        record = _validated_ledger_record(record)
    except SafeWriteError as exc:
        _generation_write_error(exc)
    entries = record["entries"]
    text_policy = record["generated_text_policy"]
    text_provenance = record.get("generated_text_provenance", [])
    text = artifact_snapshot.text
    observed_provider_markers = sorted(
        _PROVIDER_FRAGMENT_MARKER_RE.findall(text)
    )
    expected_provider_markers = sorted(
        (
            sha256_text(str(fragment["item_id"])),
            str(fragment["text_sha256"]),
        )
        for fragment in text_provenance
    )
    provider_label_count = text.count("**Unverified provider draft**")
    if (
        observed_provider_markers != expected_provider_markers
        or provider_label_count != len(expected_provider_markers)
    ):
        rows.append((
            "R-GEN-006",
            "fail",
            "visible provider-prose markers do not match the ledger provenance",
        ))
    elif text_policy == PROVIDER_UNVERIFIED_TEXT_POLICY:
        rows.append((
            "R-GEN-006",
            "info",
            f"{len(text_provenance)} provider-generated prose fragment(s) are "
            "unverified and require author review; provider/model provenance is recorded",
        ))
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
        and not _yes_has_strong_evidence(
            e,
            strict=record["provenance"]["mode"] == "strict",
        )
    ]
    if weak_yes:
        rows.append((
            "R-GEN-002",
            "fail",
            "a drafted yes lacks direct repository evidence or an exact "
            "author-confirmed manifest item at the required confidence: "
            + ", ".join(str(e.get("item_id", "?")) for e in weak_yes[:5]),
        ))
    claim = _EXECUTION_CLAIM_RE.search(text)
    if claim:
        rows.append((
            "R-GEN-003",
            "fail",
            f"the text claims execution ('{claim.group(0)}'); generated checklist "
            "and appendix ledgers do not import dynamic-run evidence",
        ))
    placeholders = sum(text.count(marker) for marker in _PLACEHOLDERS)
    if placeholders:
        rows.append((
            "R-GEN-004",
            "info",
            f"{placeholders} unresolved placeholder(s) remain — complete them before submission",
        ))
    recorded_sha = record["artifact_sha256"]
    if hashlib.sha256(artifact_snapshot.payload).hexdigest() != recorded_sha:
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
    force: Annotated[
        bool,
        typer.Option(
            help=(
                "Refresh an existing generated bundle; refuse stale, unknown, or unsafe entries."
            )
        ),
    ] = False,
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

    citation_text: str | None = None
    for candidate in ("CITATION.cff", "citation.cff"):
        source = result.repo.root / candidate
        try:
            citation_text = read_text_regular(
                source,
                label="citation source",
                parent_label="repository root",
            )
        except SafeWriteError as exc:
            _generation_write_error(exc)
        except UnicodeError:
            _generation_write_error(SafeWriteError("citation source is not valid UTF-8"))
        if citation_text is not None:
            break

    checklist_md, checklist_ledger = render_markdown(selected, result, strict=strict_evidence)
    checklist_ledger.artifact_path = "checklist.md"
    checklist_ledger.provenance["command"] = "package"

    appendix_md, appendix_ledger = appendix_report.render(result, strict=strict_evidence)
    appendix_ledger.artifact_path = "artifact_appendix.md"
    appendix_ledger.provenance["command"] = "package"

    # The manifest goes into the package only: this command never touches
    # .adduce/, so a scaffolded draft cannot masquerade as author-confirmed.
    existing_manifest = result.repo.root / ".adduce" / "manifest.yaml"
    try:
        manifest_text = read_text_regular(
            existing_manifest,
            label="manifest.yaml",
            parent_label=".adduce directory",
        )
    except SafeWriteError as exc:
        _generation_write_error(exc)
    except UnicodeError:
        _generation_write_error(SafeWriteError("manifest.yaml is not valid UTF-8"))
    if manifest_text is None:
        draft = scaffold_manifest(result.evidence)
        manifest_text = yaml.safe_dump(draft.to_dict(), sort_keys=False, allow_unicode=True)

    ledgers: dict[str, Ledger] = {
        checklist_ledger.artifact_path: checklist_ledger,
        appendix_ledger.artifact_path: appendix_ledger,
    }
    try:
        ledger_text = json.dumps(
            {key: ledger.to_dict() for key, ledger in ledgers.items()},
            allow_nan=False,
            indent=2,
        )
        checksum_text = checksums_report.render(result)
        ro_crate_text = ro_crate_report.render(result)
    except (TypeError, ValueError):
        _generation_write_error(
            SafeWriteError("submission bundle could not be serialized safely")
        )

    artifacts = [
        ("checklist.md", checklist_md),
        ("artifact_appendix.md", appendix_md),
        ("manifest.yaml", manifest_text),
        ("evidence-ledger.json", ledger_text),
        ("checksums.txt", checksum_text),
    ]
    if citation_text is not None:
        artifacts.append(("citation.cff", citation_text))
    artifacts.append(("ro-crate-metadata.json", ro_crate_text))

    # Preflight every source and payload before creating or replacing a bundle
    # artifact.  Each individual replacement is atomic; refusing a known stale
    # or unsafe entry therefore happens before any bundle content changes.
    package_dir = result.repo.root / "adduce-submission"
    try:
        package_exists = ensure_safe_directory(
            package_dir,
            label="adduce-submission directory",
        )
    except SafeWriteError as exc:
        _generation_write_error(exc)
    if package_exists and not force:
        err_console.print(
            f"[red]error:[/red] {package_dir} already exists; rerun with --force to overwrite it."
        )
        raise typer.Exit(code=2)
    expected_names = {name for name, _ in artifacts}
    if package_exists and force:
        try:
            existing_entries = list(package_dir.iterdir())
            existing_names: set[str] = set()
            for entry in existing_entries:
                metadata = entry.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise SafeWriteError(
                        "refusing symbolic-link submission bundle artifact"
                    )
                if not stat.S_ISREG(metadata.st_mode):
                    raise SafeWriteError(
                        "refusing non-regular submission bundle artifact"
                    )
                if metadata.st_nlink != 1:
                    raise SafeWriteError(
                        "refusing multiply-linked submission bundle artifact"
                    )
                existing_names.add(entry.name)
        except SafeWriteError as exc:
            _generation_write_error(exc)
        except OSError:
            _generation_write_error(
                SafeWriteError("could not inspect existing submission bundle")
            )
        if existing_names - expected_names:
            _generation_write_error(
                SafeWriteError(
                    "refusing stale or unknown entries in existing submission bundle; "
                    "review and remove the directory before regenerating"
                )
            )
    if not package_exists:
        try:
            ensure_safe_directory(
                package_dir,
                label="adduce-submission directory",
                create=True,
            )
        except SafeWriteError as exc:
            _generation_write_error(exc)

    written: list[str] = []

    def write_file(name: str, content: str) -> None:
        target = package_dir / name
        rendered = content.rstrip("\n") + "\n"
        try:
            if force:
                replace_text_regular(
                    target,
                    rendered,
                    label="submission bundle artifact",
                    parent_label="adduce-submission directory",
                )
            else:
                create_text_exclusive(
                    target,
                    rendered,
                    label="submission bundle artifact",
                )
        except SafeWriteError as exc:
            _generation_write_error(exc)
        written.append(name)

    for name, content in artifacts:
        write_file(name, content)

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
    artifacts: list[tuple[Path, str]] = []
    for _, (filename, renderer) in selected:
        target = result.repo.root / filename
        artifacts.append((target, renderer(result).rstrip("\n") + "\n"))
    if what in ("croissant", "all"):
        try:
            documents = json.loads(croissant_report.render(result))
        except (json.JSONDecodeError, TypeError, ValueError):
            _generation_write_error(
                SafeWriteError("Croissant export could not be serialized safely")
            )
        if not isinstance(documents, dict):
            _generation_write_error(
                SafeWriteError("Croissant export did not produce an object")
            )
        if not documents:
            console.print("croissant: no datasets detected or declared; add them to the manifest first.")
        for dataset_id, document in documents.items():
            safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in dataset_id)
            target = result.repo.root / f"{safe}.croissant.json"
            rendered = json.dumps(document, allow_nan=False, indent=2) + "\n"
            artifacts.append((target, rendered))

    if len({target for target, _ in artifacts}) != len(artifacts):
        _generation_write_error(
            SafeWriteError("multiple exports resolve to the same destination")
        )

    plans: list[tuple[Path, str, bool]] = []
    try:
        for target, rendered in artifacts:
            exists = regular_file_exists(target, label="export destination")
            plans.append((target, rendered, exists))
    except SafeWriteError as exc:
        _generation_write_error(exc)

    for target, rendered, exists in plans:
        if exists and not force:
            console.print(f"skipped (exists): {target}")
            continue
        try:
            if force:
                replace_text_regular(
                    target,
                    rendered,
                    label="export destination",
                    parent_label="repository root",
                )
            else:
                create_text_exclusive(
                    target,
                    rendered,
                    label="export destination",
                )
        except SafeWriteError as exc:
            _generation_write_error(exc)
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
    target = result.repo.root / BASELINE_FILENAME
    rendered = json.dumps(snapshot, allow_nan=False, indent=2) + "\n"
    try:
        replace_text_regular(
            target,
            rendered,
            label="baseline",
            parent_label=".adduce directory",
        )
    except SafeWriteError as exc:
        _generation_write_error(exc)
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
    if (
        not revision_range
        or revision_range.startswith("-")
        or any(ord(character) < 32 or ord(character) == 127 for character in revision_range)
    ):
        err_console.print(Text("error: invalid Git revision range", style="red"))
        raise typer.Exit(code=2)
    git_environment = os.environ.copy()
    for key in list(git_environment):
        if key.startswith("GIT_"):
            git_environment.pop(key, None)
    git_environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.quotePath=true",
                "-C",
                str(path),
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                revision_range,
                "--",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=git_environment,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        err_console.print(Text("error: Git diff timed out", style="red"))
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        err_console.print(Text("error: could not start Git diff", style="red"))
        raise typer.Exit(code=2) from exc
    if completed.returncode != 0:
        err_console.print(
            Text("error: Git could not evaluate the requested revision range", style="red")
        )
        raise typer.Exit(code=2)
    changed = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not changed:
        console.print(Text.assemble("no changes in ", revision_range, "."))
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
                console.print(Text.assemble("  ", file))
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
) -> list[tuple[str, str, str | None]]:
    """Resolve references; return ``(kind, identifier, sha)`` records."""
    from .cache import Cache
    from .dynamic import resolve

    cache = Cache(result.repo.root)
    if result.evidence.remote.online_attempted:
        outcomes = result.evidence.remote.resolutions
    else:
        outcomes = resolve.resolve_references(result.evidence.remote.references, cache)
        result.evidence.remote.online_attempted = True
        result.evidence.remote.resolutions = outcomes
    resolved = [(outcome.kind, outcome.identifier, outcome.sha) for outcome in outcomes]
    output_console.print(
        "[bold]Online resolution[/bold] (public metadata, from this machine, recorded in .adduce/cache)"
    )
    for outcome in outcomes:
        identifier = (
            resolve.display_url(outcome.identifier)
            if "://" in outcome.identifier
            else resolve.safe_display_text(outcome.identifier)
        )
        detail = resolve.safe_display_text(
            outcome.sha[:12] if outcome.sha else outcome.detail
        )
        style = "yellow" if not outcome.supported else ("green" if outcome.ok else "red")
        output_console.print(
            Text.assemble("  ", identifier[:70], ": ", (detail, style))
        )
    if not outcomes:
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

    sources: dict[str, tuple[str, RegularTextSnapshot]] = {}
    for file in sorted({reference.file for reference in unpinned}):
        source = result.repo.read_text(file)
        if source is None:
            continue
        try:
            snapshot = snapshot_text_regular(
                result.repo.root / file,
                label="remote-pinning source file",
                parent_label="remote-pinning source directory",
            )
        except SafeWriteError as exc:
            _generation_write_error(exc)
        if snapshot is None or snapshot.text != source:
            _generation_write_error(
                SafeWriteError("refusing changed remote-pinning source file")
            )
        sources[file] = (source, snapshot)

    resolved = _resolve_and_print(result)
    revisions = {
        (kind, identifier): sha
        for kind, identifier, sha in resolved
        if sha and kind in {"hf-model", "hf-dataset"}
    }
    if not revisions:
        console.print("nothing resolvable to pin.")
        raise typer.Exit()

    from .fixers.codemods.pin_revision import pin_revisions, unified_diff

    total_changes = 0
    for file, (source, snapshot) in sources.items():
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
            try:
                replace_text_regular_if_unchanged(
                    result.repo.root / file,
                    new_source,
                    expected=snapshot,
                    label="remote-pinning source file",
                    parent_label="remote-pinning source directory",
                )
            except SafeWriteError as exc:
                _generation_write_error(exc)
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
    timeout_minutes: Annotated[
        int | None,
        typer.Option(
            help="Per-run timeout; defaults to smoke.max_runtime_minutes, then 30."
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm executing repository code.")] = False,
) -> None:
    """Run the smoke target twice and check the runs agree (EXECUTES REPOSITORY CODE; opt-in).

    Run untrusted commands only inside a disposable, unprivileged container or
    virtual machine with external isolation. Never invoked by `adduce check`."""
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

    chosen_timeout = (
        timeout_minutes
        if timeout_minutes is not None
        else smoke.max_runtime_minutes
        if smoke.max_runtime_minutes is not None
        else 30
    )
    if not 1 <= chosen_timeout <= 24 * 60:
        err_console.print(
            "[red]error:[/red] --timeout-minutes must be between 1 and 1440."
        )
        raise typer.Exit(code=2)

    security_warning = (
        "Repository copying provides input isolation only. It does not provide "
        "process, credential, filesystem, device, resource, or network isolation. "
        "Run untrusted code in a disposable, unprivileged container or virtual "
        "machine with credentials removed, external mounts restricted, network "
        "policy applied, and CPU, memory, and process limits enforced."
    )
    err_console.print(Text(security_warning, style="yellow"))
    if not yes:
        err_console.print(
            Text(
                "No command was run. Re-run with --yes to confirm two executions "
                "with the host environment inherited."
            )
        )
        raise typer.Exit(code=2)

    from .dynamic.reproduce import reproduce as run_reproduce
    from .dynamic.reproduce import save_report

    console.print(f"running twice (seed {seed}, timeout {chosen_timeout} min/run)")
    try:
        report = run_reproduce(
            path,
            chosen,
            expected_outputs,
            seed=seed,
            timeout_minutes=chosen_timeout,
            expected_metrics=expected_metrics,
        )
        target = save_report(path, report)
    except (OSError, RuntimeError, ValueError) as exc:
        err_console.print(Text.assemble(("error: ", "red"), str(exc)))
        raise typer.Exit(code=2) from exc
    if report.agree:
        console.print(
            f"[green]runs agree[/green]: {len(report.comparable_fingerprints)} "
            "expected fingerprint(s) matched."
        )
    else:
        console.print("[red]runs disagree:[/red]")
        for line in report.disagreements:
            console.print(Text.assemble("  - ", line))
    console.print(Text.assemble("full report: ", str(target)))
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
    try:
        outcome = scaffold_fn(result)
    except SafeWriteError as exc:
        _generation_write_error(exc)
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
