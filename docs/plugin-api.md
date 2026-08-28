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
`.evaluate(ev)` and `.finding(...)`; `Finding.to_dict()` and `.items`;
`FindingItem`'s constructor fields — `id`, `status`, `message`, `confidence`,
`locations`, `remediation`, `kind` and `attributes` — and its `.to_dict()`;
`summarize_items(items)`; `Status.score_value`, `.is_applicable` and
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

Shape and rationale: [ADR 0002](adr/0002-hierarchical-findings.md).

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
or `Infinity`, which is not valid JSON. Every other field is checked at
construction too, so the offending producer is named rather than surfacing
later at the serializer: `status` must be a `Status` member, `message` and
`remediation` must be strings, `kind` must be a string or `None`, and every
element of `locations` must be a `Location`. `Finding.confidence` gets the
same non-finite check as `FindingItem.confidence`. Widening the accepted
attribute value types later is backward compatible; narrowing them would not
be. `attributes` is copied at construction and exposed as a read-only
mapping, so mutating the mapping a caller passed in afterwards has no effect
on the item.

`FindingItem` is hashable: two items with identical fields hash equal, so a
rule pack may collect them in a `set` or use them as dict keys even though
`attributes` is stored as a mapping rather than something hashable by
default.
`FindingItem.to_dict()` is the supported serialisation route;
`dataclasses.asdict()` raises `TypeError` on a `FindingItem`, because the
read-only `attributes` view cannot be copied by that path. The same failure
reaches `Finding`: `dataclasses.asdict()` also raises `TypeError` on any
`Finding` that carries an item, since `asdict()` recurses into `items` and
hits the same read-only view. `Finding` predates this change, so this is
where a caller that already used `asdict()` on a `Finding` breaks once that
finding carries items. Use `to_dict()` on both `Finding` and `FindingItem`.

`kind` is an open string rather than a closed enum, because external rule packs
need domain-specific values a shared enum could not anticipate.

### Resource policy

**Provenance of the figures in this section.** Measured 2026-08-28 against
analyzer source tree
`cae7dd33dd0077b5ecc4fe805ad707bd49e19bd2556a76204d494a5ea36ec8dd`, on python
3.14.0, darwin arm64, by `bench/finding_items.py` run with `--reps 5` over
sizes 10,000, 50,000 and 100,000. Protocol: every repetition of every size
runs in its own process, the sizes interleaved inside a repetition so machine
drift lands on all of them, and each process discards a 1,000-item warm-up
pass before the pass it records; resident growth comes from five further
probe processes per size that measure nothing else. Median of the five
repetitions reported; worst per-metric spread 15.6%, 36.5% and 9.6% at the
three sizes, in every case on a timing under 10 ms, while every byte figure was
identical across all five repetitions; machine otherwise idle. Regenerate the
analyzer digest with `python3 corpus/scripts/review_facts.py show --root .`.

0.2 guarantees an envelope of 10,000 children on one `Finding`. The figures
are for a report holding one parent finding built with that many items; the
byte sizes and resident-memory figures are properties of the report as a
whole, not of the finding in isolation. At the guaranteed envelope the JSON
report was 5.09 MiB, construction took about 29 ms, and resident memory grew
by 8,372,224 bytes holding the items.

Measured headroom extends to 100,000 items on one finding. At that size the
JSON report was 51.03 MiB, SARIF was 60.00 MiB, resident growth was
87,064,576 bytes, construction took about 304 ms, and rendering the JSON
report took about 226 ms. Per item that was 535 B in JSON and 629 B in
SARIF — both read off the serialised byte sizes above. The bench separately
reported about 744 B retained per item; that figure comes from `tracemalloc`'s
allocation accounting, not from the 87,064,576-byte peak-RSS delta, and the
bench itself notes the two are not comparable measurement bases. Do not
divide the resident-memory figure by item count and expect either result.

Some of this scales linearly across that tenfold and some does not. Linear:
the serialised byte sizes (JSON, SARIF, and the report as a whole) and
`json_dumps_seconds`, all within 1% of proportional, and `summarize_items`,
which stayed O(n) at 0.095 microseconds per item at 10,000 and 0.093
microseconds per item at 100,000. Building the parent finding
(`construction_parent_seconds`) grew 1.15× per item across that range, under
the 1.25× per-item growth the bench's `worse_than_linear` screen flags a
metric at, so it was not flagged. Converting items to a dict
(`to_dict_seconds`) grew 1.29× per item and was the only metric that screen
flagged. Concretely, extrapolating the 10,000-item `to_dict()` cost as linear
predicts 52.15 ms at 100,000 items (10 × 5.215 ms); the bench measured
67.480 ms. The bench's own explanation, offered as an account rather than a
further measurement: a larger live object graph makes CPython's generational
collector traverse more on each pass, which moves a per-item timing without
any algorithm changing.

JSON carries every item of every finding and never truncates. SARIF carries
every item of every finding it reports, and it reports only actionable
findings — `FAIL` and `PARTIAL`. A `PASS`, `UNKNOWN` or `NOT_APPLICABLE`
finding produces no SARIF result at all, so none of its items reach SARIF
either; the same finding's items still appear in full in the JSON report.
Markdown output is flat to within 4 bytes across a tenfold increase in item
count: it moved from 711 B to 715 B. That is not exactly O(1) — the census
line interpolates four integers (the total and the three non-zero per-status
counts the bench's item mix produces), and each one gains a digit going from
10,000 to 100,000 (5+4+4+4 digits becomes 6+5+5+5), which is exactly the +4
bytes observed. Rendering time is a separate quantity and is not flat:
`markdown_render_seconds` moved from 0.948 ms to 9.174 ms across the same
range, growing slightly less than proportionally to item count (0.97× per
item). Terminal output at its default verbosity renders no item at all, so
its near-flat cost (1.016 ms at 10,000 items to 1.008 ms at 100,000) is not
evidence about item cost either way. `--verbose` renders a per-finding item
census — a count and a per-status split, formatted as `"<n> item(s) not
listed here: <split>"` — never the children themselves. Its cost is not flat
because the census is O(n) in items: 2.198 ms to 10.667 ms across the same
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
without crossing it: per-item `to_dict()` cost grew 1.29× across that range.

This bound exists to contain accidental or pathological plugin output. It is
not a security boundary and must not be read as one: rule packs are trusted
in-process Python regardless of how many items they attach. See
[ADR 0004](adr/0004-rule-purity-and-output-ownership.md).

## Report schema

The JSON report carries a top-level `schema` key: `{"name": "adduce-report",
"version": 1}`. `tool.version` still records the adduce release that wrote the
file; `schema.version` identifies the report's shape, and increments whenever
that shape changes in a way that could break a consumer reading it — a key
renamed, removed, retyped, or repurposed. Version 1 is this release's finished
shape: the applicability keys, the nullable `total`, and per-finding `items`.

The shape itself is still not a covered surface — see Not covered, below — and
may change in any release. It may not change *silently* under a stationary
version number: a consumer may rely on an unchanged `schema.version` to mean
that nothing it already parses has moved beneath it, without having to track
adduce's release number to know that.

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
- The JSON report shape, including its key set. The report carries a `schema`
  key that names its shape and a version that increments on any change that
  could break a consumer (see Report schema, above), but the shape itself is
  still not a covered surface in the sense Public surface uses that word: it
  may change in any release, so long as a breaking change moves the version.

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

A rule that loads can still fail when it runs, and that failure is contained
rather than skipped. The engine warns with `RulePluginWarning` and records the
rule as `UNKNOWN` when `evaluate` raises, returns something that is not a
`Finding`, or returns a `Finding` filed under another rule's id. The run
continues and every other rule still reports. Your rule appears in the report
under its own id, unassessed, at confidence 0.0, with a message saying the check
did not complete — so it scores nothing, and the cost is a lower coverage
fraction rather than a lower score. The telemetry counter `rules.degraded`
counts these; `rules.evaluated` does not. Built-in rules are not contained this
way: a built-in that does any of the three is a defect in adduce and ends the
run.

The engine reads `id`, `category`, `title`, `weight` and `effective_severity`
once, together, before it asks anything else of your rule, and a raise from any
of them ends differently from a raise anywhere later. `effective_severity`
consults `.severity` and `.weight`, so a raise from either arrives here. Your
rule is passed over: it produces no finding, it appears in no report, and the
run counts it as `rules.skipped_unidentifiable`. The warning names your rule's
class, because there is no id to name it by. A finding is filed under an id, a
category and a title, so a rule that cannot supply them cannot be named in a
report, in a score or in a baseline, and adduce will not invent values to put
there. Define `id`, `category`, `title`, `weight` and `severity` as plain
attributes where you can. A property that computes from the filesystem, or from
state that changes between calls, can fail on the run where it matters, and the
rule is dropped when it does.

One consequence worth knowing: disabling a broken rule no longer silences it.
A profile's `disabled_rules` is matched against the id read during
identification, so a rule that fails to identify never reaches that test and is
passed over with a warning rather than skipped quietly. A disable cannot be
honoured for a rule the run is unable to name.

Your `applies_to` is guarded separately. Your rule has an identity by then, so a
raise from it is contained the way a raising `evaluate` is: `UNKNOWN` under your
own id, counted as `rules.degraded`, and suppressible through configuration. The
finding carries no source location, so no inline pragma reaches it. It is not
recorded as inapplicable. Answering `False` leaves the score untouched, and a
rule that raised reached no answer, so recording it as inapplicable would credit
your rule with a decision it never made. The cost is the same as any other
contained failure: applicable, unassessed, and a lower coverage fraction. A
built-in that raises at either point ends the run instead.

The text naming that failure is bounded like the loader's, and the name is
dropped unless it is a real identifier. Neither an exception's message nor its
class name is under adduce's control, and a class built through `type()` carries
text of any length. If your exception class has a forged or otherwise unusual
name, expect a generic phrase where you were looking for the name.

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
