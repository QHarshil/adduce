# Plugin API contract

What an out-of-tree package may import and depend on, what may change, and what
is not decided yet. [Extending adduce](extending.md) is the tutorial and shows a
working rule pack; this page is the contract behind it.

## Entry-point groups

| Group | The entry-point value resolves to | The entry-point name is | Discovered |
|---|---|---|---|
| `adduce.rules` | a module exposing `RULES`, an iterable of `Rule` subclasses | a label, used only in diagnostics | on every `discover_rules()` call |
| `adduce.reporters` | a callable taking a `CheckResult` and returning `str` | the `--format` value | once, when `adduce.report` is first imported |

Entry-point names must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}`. Rule ids must
match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}` and be unique across all loaded rules.

`adduce.rules.builtin` is reserved. adduce registers it under its own group and
the loader skips that module, because built-in rules are registered directly.

Reporter discovery runs at import time, so installing a reporter into a running
interpreter has no effect until the next process.

## Public surface

Import these names from the paths given. Every one except `__version__` appears
in its module's `__all__`; `adduce` declares no `__all__`, and `__version__` is
covered by the ordinary convention for that attribute.

| Symbol | Import from |
|---|---|
| `Rule` | `adduce.rules` |
| `Finding` | `adduce.rules` |
| `Status` | `adduce.rules` |
| `Category` | `adduce.rules` |
| `Location` | `adduce.rules` |
| `FindingItem` | `adduce.rules` |
| `summarize_items` | `adduce.rules` |
| `JsonValue` | `adduce.rules` |
| `BUILTIN_RULES` | `adduce.rules` |
| `discover_rules` | `adduce.rules` |
| `Evidence` | `adduce.evidence` |
| `collect` | `adduce.evidence` |
| `RENDERERS` | `adduce.report` |
| `ReporterPluginWarning` | `adduce.report` |
| `__version__` | `adduce` |

Covered members: `Rule.id`, `.category`, `.title`, `.rationale`, `.weight`,
`.severity`, `.fix_command`, `.effective_severity`, `.applies_to(repo)`,
`.evaluate(ev)` and `.finding(...)`; `Finding.to_dict()`; `FindingItem.to_dict()`
and `summarize_items(items)`; `Status.score_value`, `.is_applicable` and
`.is_assessed`; the attribute names on `Evidence`.

`Status` has five members. `NOT_APPLICABLE` and `UNKNOWN` both have
`score_value is None` and are both excluded from scoring, but they do not mean
the same thing: `NOT_APPLICABLE` says the check does not apply to this
repository, `UNKNOWN` says the evidence needed to decide was not found. Returning
`False` from `applies_to` is different again — the rule produces no `Finding` at
all and is invisible to every reporter.

`is_applicable` and `is_assessed` are the covered way to tell those states
apart. They are covered rather than internal because the alternative is not:
`score_value is None` holds for `NOT_APPLICABLE` and `UNKNOWN` alike, so a pack
that needs the distinction has nothing else to read. `is_applicable` is true for
every member except `NOT_APPLICABLE`; `is_assessed` is true for `PASS`,
`PARTIAL` and `FAIL`.

`FindingItem`, `summarize_items` and `JsonValue` are covered for the same
reason: a rule pack that wants to attach child results to its own findings,
and type the attributes it puts on them, has no other name to import any of
these from.

## Finding items

`Rule.finding(...)` takes a keyword-only `items` parameter: a sequence of
`FindingItem`, each one a structured observation explaining the parent finding's
verdict. Every existing positional call to `finding()` keeps working, and a rule
that passes no `items` behaves exactly as it did before this parameter existed.

`Finding` stays the unit of rule identity, scoring, category weight, baseline
tracking and suppression no matter how many items it carries. Items are never
independently scored: a rule with thousands of items still contributes exactly
its own weight to its category. `summarize_items(items)` returns a count per
`Status` member, including the members that did not occur, so a caller can index
any of the five without a presence check; it imposes no policy of its own — the
rule author still chooses the parent status.

`FindingItem.attributes` holds `JsonValue`, defined as
`str | int | float | bool | None`. Construction rejects a non-scalar attribute
value, naming the offending key rather than waiting for the JSON encoder to
fail; a duplicate `id` among the items on one `Finding`, because `id` must be
unique within its parent; and a non-finite `confidence` or a non-finite float
anywhere in `attributes`, because `json.dumps` would otherwise emit a bare `NaN`
or `Infinity`, which is not valid JSON. Widening the accepted attribute value
types later is backward compatible; narrowing them would not be. `attributes`
is copied at construction and exposed as a read-only mapping, so mutating the
mapping a caller passed in afterwards has no effect on the item.
`FindingItem.to_dict()` is the supported serialisation route;
`dataclasses.asdict()` raises `TypeError` on a `FindingItem`, because the
read-only `attributes` view cannot be copied by that path.

`kind` is an open string rather than a closed enum, because external rule packs
need domain-specific values a shared enum could not anticipate.

### Resource policy

0.2 guarantees an envelope of 10,000 children on one `Finding`. The figures
below are measured on `bench/finding_items.py` (python 3.14.0, darwin arm64, 5
reps, each size run in its own process) for a report holding one parent
finding built with that many items; the byte sizes and resident-memory
figures are properties of the report as a whole, not of the finding in
isolation. At the guaranteed envelope: the JSON report is 5.09 MiB,
construction takes about 28 ms, and resident memory grows by 8,454,144 bytes
holding the items.

Measured headroom extends to 100,000 items on one finding. At that size the
JSON report is 51.03 MiB, SARIF is 60.00 MiB, resident growth is 87,031,808
bytes, construction takes about 295 ms, and rendering the JSON report takes
about 227 ms. Per item that is 535 B in JSON, 629 B in SARIF, and about 744 B
retained.

Some of this scales linearly across that tenfold and some does not. Linear:
the serialised byte sizes (JSON, SARIF, and the report as a whole) and
`json_dumps_seconds`, all within 1% of proportional, and `summarize_items`,
which stays O(n) at 0.098 microseconds per item at 10,000 and 0.097
microseconds per item at 100,000. Building the parent finding
(`construction_parent_seconds`) grows 1.19× per item across that range, under
the 1.25× per-item growth the bench's `worse_than_linear` screen flags a
metric at, so it is not flagged. Converting items to a dict
(`to_dict_seconds`) grows 1.31× per item and is the only metric that screen
flags. Concretely, extrapolating the 10,000-item `to_dict()` cost as linear
predicts 51.76 ms at 100,000 items (10 × 5.176 ms); the bench measured
67.601 ms. The bench's own
explanation, offered as an account rather than a further measurement: a larger
live object graph makes CPython's generational collector traverse more on
each pass, which moves a per-item timing without any algorithm changing.

JSON carries every item of every finding and never truncates. SARIF carries
every item of every finding it reports, and it reports only actionable
findings — `FAIL` and `PARTIAL`. A `PASS`, `UNKNOWN` or `NOT_APPLICABLE`
finding produces no SARIF result at all, so none of its items reach SARIF
either; the same finding's items still appear in full in the JSON report.
Markdown output is genuinely O(1) with item count in bytes: across the same
tenfold increase it moves from 711 B to 715 B. Rendering time is a separate
quantity and is not flat: `markdown_render_seconds` moves from 0.954 ms to
9.749 ms across the same range, proportionally to item count (1.02× per
item). Terminal output at its default verbosity renders no item at all, so
its near-flat cost (1.004 ms at 10,000 items to 1.002 ms at 100,000) is not
evidence about item cost either way. `--verbose` terminal rendering does
render items, and its cost is not flat: 2.277 ms to 11.077 ms across the same
tenfold, a 4.9× increase.

0.2 sets no hard ceiling on item count. If one is introduced later, it should
bound items per report rather than per finding, enforced as a hard
construction error rather than silent truncation: report size and resident
memory are what a ceiling would need to contain, and both are properties of
the whole report, so a proposed ceiling of 100,000 items would apply across
every finding in a report combined, not to any one finding on its own. The
per-finding guarantee above is not a promise that an unbounded number of
findings may each carry a full envelope. A 0.3 review trigger is already
defined: an integration reporting peak resident memory as a problem, or
per-item `to_dict()` cost growing past 2× between the 10,000-item envelope and
ten times that many. The bench already reports one number under that trigger
without crossing it: per-item `to_dict()` cost grows 1.31× across that range.

This bound exists to contain accidental or pathological plugin output. It is
not a security boundary and must not be read as one: rule packs are trusted
in-process Python regardless of how many items they attach. See
[ADR 0004](adr/0004-rule-purity-and-output-ownership.md).

## Report schema

The JSON report carries a top-level `schema` key: `{"name": "adduce-report",
"version": 1}`. `tool.version` still records the adduce release that wrote the
file; `schema.version` is what identifies the report's shape, so a consumer can
tell which document version it is holding without inferring it from a release
number. Version 1 is this release's finished shape: the applicability keys, the
nullable `total`, and per-finding `items`.

The key exists; the shape it names is still not a covered surface — see Not
covered, below.

## Not covered

- Every module not named above, including `adduce.engine`, `adduce.model`,
  `adduce.scoring`, `adduce.graph`, `adduce.config`, `adduce.profiles`,
  `adduce.aeg`, and the individual modules under `adduce.report`. They import,
  and they may change in any release.
- `CheckResult` (`adduce.engine`). A reporter is handed one, but the type is not
  re-exported through any module with an `__all__`, so neither its import path
  nor its fields are covered yet. This is the largest hole in the contract.
- `Repo` (`adduce.model`), the object `applies_to` receives.
- `RulePluginWarning` (`adduce.rules.registry`), which is not re-exported from
  `adduce.rules`. `ReporterPluginWarning` is re-exported and is covered. The
  asymmetry is real, not a documentation slip.
- The field layout of the per-collector evidence dataclasses. `PythonEvidence`,
  `DependencyEvidence` and their siblings are named in `adduce.evidence.__all__`
  and import cleanly, but their fields track the collectors that build them and
  change when those change. Depend on the `Evidence` attribute names; treat each
  sub-object's fields as version-specific.
- The JSON report shape, including its key set. The report now carries a
  `schema` key that names its shape and a version number (see Report schema,
  above), but the shape itself is still not a covered surface and may change in
  any release.

If a name is not listed as covered, it is not covered. Assume nothing from its
presence in the package.

## Discovery and failure behaviour

A plugin that cannot be used is skipped with a warning. The run continues and the
built-ins remain available.

The rule loader warns with `RulePluginWarning` and skips when entry-point
metadata is unreadable, the entry-point name is invalid, loading raises, `RULES`
is missing or not iterable, iterating `RULES` raises, `RULES` contains no `Rule`
subclass, constructing a rule raises or returns a non-`Rule`, the rule id is
invalid, or the rule id collides with an already-loaded rule. A non-`Rule` entry
inside an otherwise valid `RULES` warns and is dropped while the valid classes in
the same pack still load; if iteration itself raises, nothing from that pack
registers.

The reporter loader warns with `ReporterPluginWarning` and skips when entry-point
metadata is unreadable, the name is invalid, the name collides with an
already-registered format, loading raises, or the loaded object is not callable.
The built-in formats `json`, `sarif`, `markdown`, `badge` and `latex` cannot be
shadowed.

If entry-point discovery itself fails, one warning is issued and the built-ins
are used.

Load order is sorted by entry-point name, then value, then distribution name, so
it does not depend on install order. On a collision the first in that order
holds the id or format name.

Warning text is bounded and sanitised: names and values are truncated to 80
characters and runs of other characters are replaced. Catch the warning class;
do not parse the message.

## Rule purity

A rule reads evidence and returns a finding. It does not write files, start
subprocesses, open network connections, or mutate the evidence or the
repository. Every built-in rule holds to this.

It is a convention, not an enforced boundary. `Evidence.repo` is a live `Repo`
and `Repo.read_text` performs real disk I/O, so a rule can read files from inside
`evaluate` and neither the plugin loader nor the type checker will notice.
Nothing in adduce stops a plugin from going further.

Persistence belongs outside `evaluate`: if per-item detail has to be written, the
report layer owns the write.
See [ADR 0004](adr/0004-rule-purity-and-output-ownership.md).

## Security

Third-party plugins are not sandboxed, not isolated, and not restricted. An
entry point is an ordinary Python import, so installing a plugin gives that
package the full privileges of the adduce process: filesystem, subprocess,
network, environment. The failure isolation described above keeps one broken
plugin from stopping discovery. It is not a security boundary and confines
nothing.

Install plugins you trust, on the same terms as any other dependency. When
auditing a repository you do not trust, use an environment with no third-party
plugins installed.
See [Security model](security-model.md#extension-and-supply-chain-risk).

## Versioning and change policy

- Both group names, `adduce.rules` and `adduce.reporters`, are stable. Renaming
  either is a breaking change.
- While the version is 0.x, a covered symbol is removed, renamed, or changed in
  meaning only on a minor bump, announced in [CHANGELOG.md](../CHANGELOG.md) one
  minor release ahead, with a `DeprecationWarning` where the symbol can carry
  one.
- Additive changes may land in a patch release: a new covered symbol, a new
  `Category` member, a new `Finding` field with a default, a new keyword-only
  parameter with a default.
- A rule of the shape `applies_to` plus `evaluate(ev) -> Finding` keeps working
  without a rewrite. Any child-result model is additive.
- Uncovered modules change without notice and without a deprecation window.

## Decided but not built

`PROPOSED` means designed and accepted, not built. None of the following exists
today; do not write a plugin against it.

**A stable public facade, `adduce.api` — `PROPOSED`.** A module that re-exports
the covered names with an explicit `__all__` and no logic of its own, so the
surface promised is the surface a contract test imports. Existing import paths
would keep working; it adds a namespace rather than moving one. Deferred to
after this release by the project's own sequencing, not because anything in
the model it would export is still unfinished.
See [ADR 0003](adr/0003-public-extension-api-stability.md).
