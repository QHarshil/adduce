# Architecture

How adduce is put together today, subsystem by subsystem, with the state of each
one stated plainly. Read it to decide where a change belongs. For what adduce is
for, start at [Concepts](concepts.md); for what it refuses to claim,
[Honest limits](honest-limits.md).

Every subsystem carries exactly one state label — `IMPLEMENTED`,
`PARTIALLY IMPLEMENTED`, `PROPOSED` (designed and accepted, not built),
`DEFERRED TO 0.3`, or `REJECTED BY MEASUREMENT`. They are collected in the
[subsystem states](#subsystem-states) table at the end.

## The check pipeline

`engine.py::run_check` runs these stages in this order, each inside the
telemetry span named in the left column.

| Stage | Module | Produces |
| --- | --- | --- |
| `scan` | `model.py::scan_repository` | `Repo` — file inventory and git metadata |
| (collection) | `evidence/__init__.py::collect` | `Evidence` — one span per collector |
| `resolve.online` | `dynamic/resolve.py::resolve_references` | remote resolutions; only under `--online` |
| `collect.latex.paper` | `evidence/latex.py::collect_latex` | LaTeX evidence from an out-of-tree paper; only under `--paper` |
| `rules.discover` | `rules/registry.py::discover_rules` | built-in rules plus `adduce.rules` plugins |
| `rules.evaluate` | the loop in `engine.py` | `list[Finding]` |
| `score` | `scoring.py::score` | `ScoreCard` |
| `graph` | `graph.py::build_graph` | `ClaimGraph` |
| `reviewer_time` | `reviewer_time.py::estimate` | `ReviewerTime` |

Only `resolve.online` and `collect.latex.paper` are optional, and both are off
unless the caller asks; see the [security model](security-model.md) for the
boundary each online layer crosses. `build_graph` builds the **claim graph** —
the metric-to-command-to-config trail from [Concepts](concepts.md). It is not
the Artifact Evidence Graph; `engine.py` contains no reference to `aeg`.

## Evidence collection

Fourteen `collect_*` collectors fill one `Evidence` object: config, data,
dependencies, environment, docs, portability, git, notebook, precision, results,
latex, run_history, remote, python_ast — this list is not the order they run in.
Real dependencies exist between them regardless of how they are listed: Python
evidence feeds framework detection, data, precision and remote; docs evidence
feeds git.

**Collection is not single-pass.** Three collectors run behind one shared read
pass (`content.py::scan_once`) — the Python AST analysis, the remote-text scan
and the portability line scan, the three that all want source text. Each wanted
file is read once, handed to every consumer, and released before the next, so
peak text held is one file. `ADDUCE_DEBUG_STRICT=1` makes a second read of the
same path within a pass an error rather than a silent regression.

The other eleven collectors each walk and read on their own through
`Repo.read_text`, backed by a 512-entry LRU (`model.py:170`). `content.py`
argues that a bounded cache cannot fix repeated reads at repository scale: the
passes are sequential and each covers the whole tree, so anything smaller than
the working set is evicted before the next pass reaches it — which is what the
512-entry cache did, at a 0.3 % hit rate. That measurement is why the shared
pass exists. Migrating the remaining eleven is open work, and it is throughput
rather than correctness. See
[ADR 0007](adr/0007-collection-is-partly-single-pass.md).

## Rule evaluation

78 built-in rules across 17 categories ([rule reference](rules/README.md)), plus
whatever the `adduce.rules` entry-point group supplies. Two tests run before
evaluation:

1. `rule.id in profile.disabled_rules` → counted as `rules.skipped_disabled`, skipped.
2. `not rule.applies_to(repo)` → counted as `rules.skipped_inapplicable` (`engine.py:236`), skipped.

**A rule skipped by either branch produces no `Finding` at all.** It is absent
from the result set, from both scoring denominators, and from every finding list
a report renders. `rules.skipped_inapplicable` also reaches the score card, as
`evidence_base.rules.skipped_inapplicable` in the JSON report, so the number of
rules that never ran is now recoverable from output; `rules.skipped_disabled`
remains telemetry only. `adduce check .` against this repository returns 69
findings and skips a further 9 rules this way.

Every rule that is considered contributes exactly one `Finding` carrying one of
five statuses. The rule normally returns it. If a rule that is not one of
adduce's own raises, returns something that is not a `Finding`, or files one
under another rule's id, the engine contains the failure, warns, and records an
`UNKNOWN` finding under that rule's own id in its place, so one installed rule
pack cannot end the run (`engine.py:120-154`). A built-in doing any of the three
is a defect in adduce and is not contained. `UNKNOWN` and `NOT_APPLICABLE` do
not mean the same thing — `UNKNOWN` means the check applied and reached no
assessment, `NOT_APPLICABLE` means it did not apply — and scoring now
separates them with the `Status.is_applicable` and `Status.is_assessed`
predicates rather than with a `score_value is None` test, which cannot tell
the two apart. A category left applicable and wholly unanswered is kept on
the score card rather than dropped, and coverage divides assessed findings by
applicable ones rather than by every finding returned. Both follow
[ADR 0001](adr/0001-status-applicability-and-assessment-coverage.md), and
both are in [scoring.md](scoring.md).

Rule purity is a convention, not an enforced boundary. No built-in rule module
imports or calls a filesystem, subprocess or network API — but `Evidence.repo`
is a live `Repo` and `Repo.read_text` performs real disk I/O, so a third-party
rule can read files from inside `evaluate` and neither the loader nor the type
checker notices. See
[ADR 0004](adr/0004-rule-purity-and-output-ownership.md).

## Scoring

`scoring.py::score` groups findings by category, sums `status.score_value *
finding.weight` over the findings that have a score value, normalises each
category to its profile weight, and renormalises the total over the categories
that survive. Rule weights span 1–8. Below `MINIMUM_ANALYSABLE_LINES` (100) the
card comes back unrated. Tier thresholds, the category-drop behaviour, coverage
arithmetic and the measured status mix are in [scoring.md](scoring.md); this
page does not repeat them.

## Reporting

Thirteen modules live under `report/`, and they are not all the same kind of
thing. Five are registered in `RENDERERS` and are selectable as `--format`
values: `json`, `sarif`, `markdown`, `badge`, `latex`. `terminal` is the default
format and is called directly rather than through `RENDERERS`, so `--format`
offers six choices in all. The remaining seven — appendix, checksums, codemeta,
croissant, ro_crate, software_heritage, zenodo — are output surfaces reached by
their own CLI commands, not by `--format`.

Any `CheckResult -> str` callable registered under the `adduce.reporters` group
joins `RENDERERS` as a further `--format` value named after its entry point.
Every reporter reads the finished `CheckResult`; none can change a verdict.
The JSON report carries a top-level `schema` key, `{"name": "adduce-report",
"version": 1}`, proposed in
[ADR 0003](adr/0003-public-extension-api-stability.md) and now landed. The
report's shape changed twice in 0.2 — the `evidence_base` keys and the
`FindingItem` serialisation shape — and version 1 is the finished shape both
changes produced, so one key stamps it.

## The Artifact Evidence Graph

A typed, offline, deterministic view of what was detected, each fact carrying
its resolution method and confidence. It is **diagnostic**: its only production
path is the `adduce graph` command, and building it changes nothing about a
check. Schema, identity rules and the confidence ceiling are in
[aeg-schema.md](aeg-schema.md).

Real and enforced: the confidence ceiling, validated from both the node and edge
constructors; retain-and-ignore handling of unknown types; and producers that
emit 10 of the 19 declared node types.

Declared but unreachable: `builtin_producers()` is a hardcoded pair (config and
Python), and although producer ownership validates a `plugin:<name>` string, no
node is ever constructed with a `plugin:*` owner. The stored-graph major-version
check has no live caller, because nothing in the shipped code reads a stored
graph. `UNTRUSTED_METHODS` has a definition, a re-export and no consumers.
`NodeType.SYMBOL` appears once in `src/adduce/` — its own definition. See
[ADR 0006](adr/0006-aeg-remains-diagnostic.md).

## Claim extraction

There is no `adduce claims` command. Claim drafting is reached through
`adduce manifest`, which writes `.adduce/manifest.yaml` via
`manifest_builder.py` from the LaTeX and Markdown evidence the collectors
gathered.

The dedicated candidate-extraction, normalisation and duplicate-clustering layer
is in development and is not part of this line. Also undelivered: author
confirmation of extracted candidates, lexical retrieval, symbol and config-graph
retrieval, semantic rerank, four-way link classification, calibrated abstention,
and source-located explanation.

Its effectiveness is measured against a labelled development set, and that
measurement is not complete: the zero-high-confidence-false-positive acceptance
criterion is **not met**, and no effectiveness figure is stated as a result.
Drafted claims are scaffolding, not claim discovery.

## Extension surfaces

Two public entry-point groups, shown in [extending.md](extending.md) and
specified in [plugin-api.md](plugin-api.md):

| Group | Contract | Loader |
| --- | --- | --- |
| `adduce.rules` | a module exposing a `RULES` iterable | `rules/registry.py` |
| `adduce.reporters` | a `CheckResult -> str` callable | `report/__init__.py` |

Discovery failure is non-fatal in both: a bad plugin is skipped with a warning
and the built-ins stay available. Plugins are **not** sandboxed, isolated or
restricted. Entry points import plugin packages, so an installed plugin runs as
trusted in-process Python with the invoking user's privileges. Install plugins
you trust.

## Subsystem states

| Subsystem | State | Note |
| --- | --- | --- |
| Repository scan | `IMPLEMENTED` | `model.py::scan_repository` |
| Evidence collection | `IMPLEMENTED` | 14 collectors, one `Evidence` object |
| Shared content pass | `PARTIALLY IMPLEMENTED` | covers 3 of the 14 collectors |
| Content cache as the whole-repository read fix | `REJECTED BY MEASUREMENT` | 0.3 % hit rate; the cache remains for the other 11 collectors |
| Rule discovery and evaluation | `IMPLEMENTED` | 78 rules, 17 categories, plus plugins |
| Rule purity contract | `PARTIALLY IMPLEMENTED` | held by every built-in rule, enforced by nothing |
| Applicability-aware coverage | `IMPLEMENTED` | [ADR 0001](adr/0001-status-applicability-and-assessment-coverage.md); coverage is assessed over applicable findings |
| Hierarchical findings (`FindingItem`) | `IMPLEMENTED` | [ADR 0002](adr/0002-hierarchical-findings.md) |
| Scoring | `IMPLEMENTED` | [scoring.md](scoring.md) |
| Reporters | `IMPLEMENTED` | 13 modules: 5 in `RENDERERS`, terminal direct, 7 own-command surfaces |
| JSON report schema and version keys | `IMPLEMENTED` | [ADR 0003](adr/0003-public-extension-api-stability.md) |
| Public re-export namespace and deprecation policy | `PROPOSED` | [ADR 0003](adr/0003-public-extension-api-stability.md) |
| Claim graph | `IMPLEMENTED` | `graph.py`, built on the check path |
| Artifact Evidence Graph | `PARTIALLY IMPLEMENTED` | diagnostic only; 10 of 19 node types |
| AEG producer plugins | `PROPOSED` | no `plugin:*` owner is ever constructed |
| Claim drafting into the manifest | `PARTIALLY IMPLEMENTED` | dedicated extraction and clustering layer not on this line |
| Claim–artifact retrieval, rerank, link classification, abstention | `DEFERRED TO 0.3` | open-ended research, out of scope for 0.2 |
| Reviewer-time estimate | `IMPLEMENTED` | `reviewer_time.py` |
| Online resolution, `pin-remotes`, `reproduce` | `IMPLEMENTED` | opt-in and fenced; [security-model.md](security-model.md) |

The decision behind each non-`IMPLEMENTED` row is recorded in the
[ADR index](adr/0000-index.md).
