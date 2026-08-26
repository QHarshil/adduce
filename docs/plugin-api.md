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
| `BUILTIN_RULES` | `adduce.rules` |
| `discover_rules` | `adduce.rules` |
| `Evidence` | `adduce.evidence` |
| `collect` | `adduce.evidence` |
| `RENDERERS` | `adduce.report` |
| `ReporterPluginWarning` | `adduce.report` |
| `__version__` | `adduce` |

Covered members: `Rule.id`, `.category`, `.title`, `.rationale`, `.weight`,
`.severity`, `.fix_command`, `.effective_severity`, `.applies_to(repo)`,
`.evaluate(ev)` and `.finding(...)`; `Finding.to_dict()`; `Status.score_value`,
`.is_applicable` and `.is_assessed`; the attribute names on `Evidence`.

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
- The JSON report shape, including its key set. See below.

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
would keep working; it adds a namespace rather than moving one. Held back
because the model it would export changes in 0.2, and publishing the namespace
twice with different contents is worse than publishing it once late.
See [ADR 0003](adr/0003-public-extension-api-stability.md).

**A JSON report schema version — `PROPOSED`.** The report carries no schema or
format-version key. `tool.version` records the adduce release that produced the
file; it does not identify the report's shape. The shape changes twice in 0.2.
The first has landed: the `evidence_base` block gained `applicable_rules` and a
nested `rules` block, and `total` became nullable. The second, the `FindingItem`
serialisation shape, has not. The version key is held back to land with the
second, so a single key stamps the finished 0.2 shape rather than one stated now
and contradicted when structured findings settle. Until it lands, `tool.version`
and the release notes are what identify the shape a file carries.

**Structured child findings — `PROPOSED`.** A non-recursive list of per-item
results carried on the parent `Finding`, so a rule checking many individual
assertions can report each one instead of writing a sidecar file from inside
`evaluate`. `Finding` would stay the unit of rule identity, scoring, baseline
tracking and suppression; items are explanatory and never independently scored.
Shape and rationale: [ADR 0002](adr/0002-hierarchical-findings.md).
