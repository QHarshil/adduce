# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Validation status

The `r6` effectiveness preregistration lock is retired. Its analyzer digest
binds the `src/adduce` byte tree, and the work in this release rebuilds that
tree. Protocol amendment 8 does not register a successor: amendments 5, 6 and 7
each answered a byte change by re-locking, which is the right answer when the
analyzer is settled and the wrong one while it is being rebuilt on purpose.
Enumerating every edit against a dead lock recreates the bookkeeping of a
preregistration without its guarantee.

Amendment 8 opens an unlocked development interval instead, and the line it
draws is experimental data against analysis machinery. The analyzer source and
its rules, the built-in rule inventory, the preregistration and report schemas,
the run harness and the analysis-plan documentation are free to change, and no
change to them is enumerated here. The study's material stays frozen and moves
only under a further dated amendment: the repository inventory, the clone
manifest and clone snapshot set, the badged-provenance identities, and the
frozen claim truth `9a26d06c…`, which may be checked for integrity but never
tuned against. Four executable assertions replace the lock — the tracked
inventory and provenance digests, the gitignored study digests wherever the
local corpus is present, and the refusal of an effectiveness run that binds no
live lock. A successor lock is registered under a dated amendment against the
finished analyzer, and no effectiveness, calibration, or false-positive figure
is stated in the meantime.

One correction from this release outlives the lock that recorded it, because it
concerns whether digests are comparable at all. The helper that hashes the
`src/adduce` byte tree ordered files by comparing `Path` objects, and that
comparison casefolds on Windows and does not on POSIX, so the analyzer digest
every preregistration binds depended on the host that computed it. All three
copies of the helper now order by the segments of the relative POSIX path. The
current tree's digest is byte-identical either way, so no recorded digest moves,
and a test holds the three copies to one value. The copies are deliberate: the
built-in checker runs as a sandboxed child process that imports nothing from its
siblings.

This release makes **no final effectiveness claim**, and deliberately ships with
no preregistration lock. That is not an exception: `docs/releasing.md`'s first
gate is satisfied by completing the version's corpus and human-review gates *or*
by documenting explicitly which validation remains developmental, and this is the
second path. The claim-extraction figures are developmental status rather than
results. Pooled recall is **141/296 = 47.6%** over the 20 labelled pairs.
Precision is **700/1156 = 60.6%** over the **6 of 34** pairs adjudicated so far,
with **122** high-confidence false positives pooled and exactly one pair reaching
zero. **The zero-high-confidence-false-positive acceptance criterion is not met.**
The remaining claim-resolution stages, the effectiveness acceptance criteria, and
the preregistered validation report belong to the following release.

### Added

- Added per-stage timing and work counters to the check pipeline, reported by
  `adduce check --timings` and as a `telemetry` block in the JSON report. A
  default offline run records 23 stage durations and 9 counters. Durations differ
  between identical runs, so they are omitted unless requested and the default
  report remains byte-for-byte stable. Measured cost on the largest corpus
  repository, per operation rather than by subtracting two whole-run timings:
  11.6 µs for the stage timers, 1.1 ms for counter recording over 6,282
  inventoried files, and 3.3 µs for a snapshot — about 0.005% of a 21-second run.
- Added a benchmark harness under `bench/` with a committed baseline covering
  five real repositories, this repository, and the fourteen synthetic
  positive-control repositories. It records cold runtime, repeat runtime, peak
  resident memory with its platform's unit, files and lines per second, per-stage
  timings, disk reads per inventoried file, parser failures, and whether a
  repeated run renders byte-identically. A target that is absent or fails to
  measure is recorded with the reason rather than defaulted. `bench/runner.py
  compare` fails on a regression and now gates CI.
- Added `src/adduce/content.py`, which walks the inventory once and hands each
  file's text to every collector that wants it. Setting `ADDUCE_DEBUG_STRICT=1`
  makes a second read of the same path within one pass an error rather than a
  silent regression.
- Added `bench/runner.py finding-diff`, which enumerates every rule status that
  honouring `.gitignore` moves, and classifies each move as a rule that stopped
  applying, became not-applicable, dropped, or improved. It exists so the
  behaviour change below is reproducible rather than asserted.

- **Three reference documents describing the analyzer as it is, not as designed.**
  `docs/architecture.md` walks the pipeline stage by stage and closes with a
  20-row subsystem table; `docs/plugin-api.md` states the contract for both public
  entry-point groups; `docs/scoring.md` covers how a repository becomes a number.
  Every subsystem carries one of `IMPLEMENTED`, `PARTIALLY IMPLEMENTED`,
  `PROPOSED`, `DEFERRED TO 0.3` or `REJECTED BY MEASUREMENT`, so a reader cannot
  mistake a design for shipped behaviour. Several long-standing overstatements are
  corrected in the process: collection is not single-pass — 3 of the 14 collectors
  share one read pass and the other 11 read through a 512-entry LRU measured at a
  0.3% hit rate — the evidence graph is diagnostic and `engine.py` holds no
  reference to it, and third-party plugins are not sandboxed.
- **Contributor infrastructure.** Issue forms for bug reports, false positives and
  API or schema changes; a pull request template; `CODEOWNERS` assigning review by
  subsystem; and a Dependabot configuration. Auto-merge is deliberately not
  enabled: the validation corpus preregistration binds a SHA-256 over the
  analyzer's exact dependency versions, so an unattended bump would silently void
  every lock registered against the old set.
- **A contract test that audits the plugin surface through a real installed
  distribution.** `tests/fixtures/external_plugin` is a separately installable
  package registering both public groups, discovered through `importlib.metadata`
  exactly as any third-party pack would be — nothing monkeypatches entry points or
  constructs one by hand. It also registers a reporter named `json`, which must
  lose, so built-in format shadowing is verified rather than assumed. The CI job
  sets `ADDUCE_REQUIRE_EXTERNAL_PLUGIN=1`, which turns "plugin not installed" from
  a skip into a failure; without it a broken install step would leave the job green
  over five tests that never ran.
- **A lowest-direct-dependency CI job.** The compatibility matrix installs whatever
  the resolver picks, which is the newest release of everything, so it tests the
  ceiling and never the floor. This job installs the oldest combination the project
  claims to support, on the lowest supported Python. Its constraints are derived
  from `pyproject.toml` at job time rather than committed separately, so raising a
  floor cannot leave a stale pin behind, and a dependency declared with no lower
  bound fails the step instead of being dropped from the constraints and silently
  untested. It found a real defect on its first run — see Changed.
- **An aggregate CI check.** One required status for a pull request, covering every
  job except `pypi-smoke`, which is dispatch- and schedule-only and skips on pull
  requests. It treats skipped and cancelled as failure, because a skipped required
  check reads as "not failed" on branch protection.
- **Findings can now carry structured child observations.** `FindingItem` is a
  frozen, non-recursive record — `id`, `status`, `message`, `confidence`,
  `locations`, `remediation`, an open `kind` string, and `attributes` restricted
  to JSON scalars (`str | int | float | bool | None`; a nested dict, list, or
  bytes value is rejected at construction, naming the offending key).
  `Rule.finding(...)` gained a keyword-only `items` parameter. Every existing
  positional call keeps working, and a rule that returns one `Finding` with no
  items behaves exactly as it did before this parameter existed. `id` must be
  unique within its parent — a duplicate is rejected at construction, as is a
  non-finite `confidence` or a non-finite float anywhere in `attributes`,
  because `json.dumps` would otherwise emit a bare `NaN` or `Infinity`, which is
  not valid JSON. `Finding` stays the sole unit of rule identity, scoring,
  category weight, baseline tracking and suppression no matter how many items
  it carries: items are never independently scored. `summarize_items(items)`
  returns a per-status count over all five `Status` members, including the ones
  that did not occur, so a caller can index any of them without a presence
  check; it imposes no policy of its own, the rule author still chooses the
  parent status. `Finding.to_dict()["items"]` is always present and `[]` when
  empty.

  0.2 guarantees an envelope of 10,000 children on one finding. Measured on
  this machine (python 3.14.0, darwin arm64, `bench/finding_items.py`, 5 reps,
  each size in its own process): at 10,000 items the JSON report is 5.09 MiB,
  construction takes about 31 ms, and resident memory grows by 8,486,912 bytes
  holding the items. Measured headroom extends to 100,000 items on one
  finding: the JSON report is 51.03 MiB, SARIF is 60.00 MiB, resident growth
  is 86,999,040 bytes, and construction takes about 326 ms. Byte sizes and
  `summarize_items` scale linearly across that range; building the parent
  finding grows 1.16× per item, under the 1.25× per-item growth the bench's
  `worse_than_linear` screen flags a metric at, so it is not flagged.
  Converting items to a dict does not — `to_dict_seconds` grows 1.30× per
  item, which the bench attributes to CPython's generational collector
  traversing a larger live object graph rather than to any change in
  algorithm. JSON carries every item of every finding and never truncates.
  SARIF carries every item of every finding it reports, and it reports only
  actionable findings — `FAIL` and `PARTIAL`; a `PASS`, `UNKNOWN` or
  `NOT_APPLICABLE` finding produces no SARIF result, so none of its items
  reach SARIF.

  The human formats never list children. `markdown`, `latex`, and `adduce
  check --verbose` each add one line to a finding that carries items —
  `"<n> item(s) not listed here: <split>"`, a count per status — and no
  child's content appears in any of them; `markdown` and `latex` show this
  line for every finding that carries items, terminal only under `--verbose`.
  Markdown's bytes are flat to within 4 bytes across the same tenfold
  increase (711 B to 715 B); rendering time is a separate quantity and is not
  flat, moving from 1.021 ms to 9.786 ms across the same range, growing
  slightly less than proportionally to item count. Terminal output at its
  default verbosity carries no item detail at all, and its near-flat cost
  (1.021 ms to 1.045 ms across the same range) is not evidence about item
  cost either way; `--verbose` renders the item census, and its cost grows
  from 2.355 ms to 11.482 ms, a 4.9× increase, because the census is O(n) in
  items. 0.2 sets no hard ceiling on
  item count; if one is introduced later it should bound items per report,
  not per finding, enforced as a hard construction error rather than silent
  truncation: report size and resident memory are what it would need to
  contain. The bound exists to contain accidental or pathological plugin
  output. It is not a security boundary — rule packs remain trusted
  in-process Python regardless of how many items they attach.

### Changed

- **The declared minimum `typer` is now 0.16.0, not 0.12.** The old bound was not a
  true floor and nothing caught it, because every environment that ever ran
  installed a much newer typer. Measured on Python 3.10 with every other dependency
  at its declared floor: **typer 0.12.0 fails 109 tests**, 0.15.4 fails 2, and
  0.16.0 passes the suite at 1,574 passed. Two separate causes — typer could not
  translate a `str | None` annotation before 0.13, and through 0.15.3 it called
  click's `make_metavar()` with an arity current click rejects. This raises a
  declared minimum to a true one; it does not change what a normal install
  resolves. No other floor moved: `rich` 13.0, `jinja2` 3.1, `pyyaml` 6.0 and
  `libcst` 1.1 were each verified to work at the floor.
- **The source distribution now carries the contributor files `CONTRIBUTING.md`
  links to** — the issue forms, the pull request template, `CODEOWNERS` and the
  Dependabot configuration. They resolved in a checkout and dangled in the archive,
  which the extracted-sdist link check exists to catch and did.
- Superseded pull-request CI runs are now cancelled. Pushes to `main` and `dev` and
  the weekly schedule still run to completion, because their results are read after
  the fact rather than watched. Every checkout sets `persist-credentials: false`,
  and pip downloads are cached against `pyproject.toml`.


- **Each source file is now read and decoded once per run, not three times.**
  The AST analysis, the portability scan, and the remote-reference scan each
  walked the repository independently, so every Python file was opened, decoded
  from UTF-8, and split into lines three times over. Measured on the largest
  corpus repository: 15,583 reads over 6,246 files, 4,648 of them read exactly
  three times. The inventory is now walked once and each file's text is handed
  to every collector that wants it, then released before the next file is read.

  Reads per file fall from **2.48 to 1.01**, and cold runtime from **21.7 s to
  16.7 s** at the largest stratum and 1.63 s to 1.39 s at the medium one.
  Verified to change no output: the JSON report is byte-identical across all
  fifteen pinned corpus repositories and all fourteen synthetic ones. The
  thirtieth target is adduce's own tree, where the only difference is its own
  changed line count and no rule status moves.

  Caching the text was considered and rejected on measurement. The passes were
  sequential and each covered the whole repository, so any cache smaller than
  the working set is evicted before the second pass reaches it — which is what
  the previous 512-entry cache did, at a 0.3% hit rate. Holding everything was
  not available either: the decoded Python source of the largest corpus
  repository is 135 MB, and reducing peak memory is a goal of this work, not a
  budget to spend. Sharing the read in time costs one file of text at a time.

  Peak memory is **unchanged** by this, and the runtime reduction is well short
  of the 40% the plan projected for the stratum. Both follow from the same
  measured fact: reading and decoding was about a second of a twenty-second run.
  The remainder is AST traversal, which is a separate change.

  `collect_python`, `collect_portability`, and `collect_remote` keep their
  signatures and still walk and read on their own, so a plugin or a caller
  outside the shared pass is unaffected. A test asserts the two paths agree.
- **`.gitignore` is now honoured by default.** An ignored `data/`, `wandb/`,
  `outputs/`, or vendored checkout is not part of the artifact under review, so
  it is no longer scanned and can no longer contribute findings. `--no-gitignore`
  restores the previous whole-tree scan. This changes findings on any repository
  with an ignored tree; see the correctness fix below for the measurement. Git's
  own matcher decides, so nested ignore files, negation, anchoring, and
  directory-only patterns behave exactly as git does, and a tracked file matching
  an ignore pattern is still scanned.

  It never scans less than it reports: a scan root that is itself gitignored
  keeps every file, and an unavailable git, a failed query, or a directory that
  is not a repository leaves the whole tree in scope. System and global git
  config are suppressed for the query, so a user's `core.excludesFile` cannot
  silently shrink an audit.

  The answer costs two read-only git queries per scan, about 12 ms, and that is
  a fixed cost rather than a proportional one. On a repository with nothing
  ignored it is therefore pure overhead: measured on the smallest corpus
  repository, 26 files, a run goes from 72.8 ms to 84.7 ms, and by 122 files it
  is no longer distinguishable from noise. Repositories that do ignore a tree
  gain far more than they pay — 24.6 s to 1.2 s in the case below.
- `Repo` exposes `read_cache_stats()`, and `evidence.collect` accepts an optional
  `telemetry` keyword. Both are additive; rule and reporter plugins are
  unaffected.
- The JSON report gains an `evidence_base` block recording `rated`,
  `evaluated_rules`, `considered_rules`, `applicable_rules`, `coverage_percent`,
  `analysable_lines`, and a nested `rules` object holding `assessed`, `unknown`,
  `not_applicable` and `skipped_inapplicable`, so a consumer can see how much the
  score rests on rather than inferring it. Those four counts are the four
  outcomes a registered rule can reach; `skipped_inapplicable` counts the rules
  whose `applies_to` returned `False`, which produce no finding at all and enter
  neither denominator. On this repository they read 51, 2, 16 and 9, which is why
  96.2% coverage is not a statement about all 78 built-in rules. `ScoreCard`
  gains the matching fields — `applicable_rules` and `skipped_inapplicable`, plus
  `unknown_rules`, `not_applicable_rules` and `coverage` as properties — all
  defaulted, and `score()` takes optional `analysable_lines` and
  `skipped_inapplicable` keywords; a caller that omits them still gets a tier, so
  plugins scoring findings directly are unaffected.
- **Assessment coverage now divides by applicable findings, not by every finding
  returned.** `Status` gained `is_applicable` (`PASS`, `PARTIAL`, `FAIL`,
  `UNKNOWN`) and `is_assessed` (`PASS`, `PARTIAL`, `FAIL`). Both are membership
  tests over the enum members rather than tests of `score_value`, because
  `NOT_APPLICABLE` and `UNKNOWN` both carry `score_value is None` and one test
  cannot separate them. Coverage is `assessed / applicable`.

  Migration, measured on this repository:

  ```
  old:  51 assessed / 69 returned findings   = 73.9 %
  new:  51 assessed / 53 applicable findings = 96.2 %
  ```

  **This is a denominator correction, not improved effectiveness.** The same 51
  checks reach the same 51 assessments on the same repository, and nothing
  further is assessed. The 16 `NOT_APPLICABLE` findings never applied and should
  not have reduced coverage. adduce does not assess 22 percentage points more
  evidence than it did before, and a `coverage_percent` recorded under an earlier
  release cannot be compared with one recorded under this release.

  Weighted coverage stays a backlog measurement rather than a second metric, and
  **adduce reports no weighted coverage number**. On this repository it would
  read 157.0 / 161.0 = 97.5%, a divergence of 1.3 percentage points from the
  count-based figure. Measurement reopens the question: either more than 20% of
  measured repositories diverging by more than 5 percentage points, or a corpus
  p95 absolute divergence above 10 percentage points.
- **`ScoreCard.total` and the JSON `total` may now be `null`**, and only when no
  check anywhere reached an assessment. A card on which every finding is a `FAIL`
  still reports `0.0`: a measured zero and no measurement are different results,
  and the type now says so. In the `null` case the tier reads
  `Unrated (nothing assessed)`, which is distinct from
  `Unrated (insufficient evidence)` — the first is source no check could assess,
  the second too little source to judge, and a reader handed the wrong one looks
  in the wrong place for the cause. `--fail-under` against a card with no score
  reports that the threshold could not be evaluated because no check reached an
  assessment, and exits 1 rather than comparing against an invented zero.
- **The `evidence_base` and `total` changes above are breaking changes to the
  JSON report for existing consumers, and the report gained a further breaking
  change alongside them.** `total` can be `null` where it was always a number,
  and `coverage_percent` keeps its name while its denominator changes. The
  report also gained a top-level `schema` key, `{"name": "adduce-report",
  "version": 1}`, so a consumer that asserts an exact top-level key set has to
  admit it. `tool.version` still identifies the adduce release that wrote the
  file, not the report's shape; `schema.version` is what now names the shape,
  and version 1 covers the applicability keys, the nullable `total`, and
  per-finding `items` together, so one key stamps the finished 0.2 shape rather
  than a half-versioned contract. Inside this repository,
  `corpus/scripts/run_contract.py` is exactly such a consumer and was updated to
  accept the new key.
- `corpus/scripts/run_contract.py` no longer recomputes a tier from a score
  unconditionally. That invariant held only while a tier was a pure function of
  the score, and enforcing it would now reject a correct artifact for any
  repository the analyzer could not read. It validates the `evidence_base` block
  and checks the scored population exactly instead, and its exact key set and its
  total and tier reconstruction were extended again to accept `applicable_rules`,
  the nested `rules` block, and a `total` that may be absent.
- **The effectiveness runner no longer carries a default preregistration lock.**
  `corpus/scripts/run_validation.py` hard-coded the void `r6` record as the lock
  every effectiveness run bound to. A retired lock stays on disk as a record of
  what was believed and when, but it is not a lock: the runner resolves a live
  lock from `ADDUCE_CORPUS_PREREGISTRATION`, and with none registered it refuses
  the run before reading or writing anything, naming `--operational-only` as the
  alternative. There is deliberately no provisional unlocked-effectiveness
  artifact — a run that binds nothing is not weaker evidence about analyzer
  quality, it is none, and refusing the operation says so where a disclaimer on
  an artifact would not. The path stays covered by a synthetic fixture lock:
  protocol IDs beginning `synthetic-` are reserved, and the run contract rejects
  such a lock over any run under `corpus/outputs`, so a fixture run cannot be
  produced under, or validated from, that tree. The guard is a contract check,
  not a filesystem permission: it does not stop bytes being copied there by
  other means.

  One provenance field moves as a consequence, in every run including
  `--operational-only`. The corpus-harness dirty and tracked flags are computed
  over the required harness files plus the live lock, and with no live lock they
  are computed over the harness files alone — so editing a retired lock on disk
  no longer marks the harness dirty, where it previously did. That is the
  correct reading, because a retired lock is not part of the harness, and it is
  recorded provenance rather than enforcement: the run contract gates those
  fields only for an effectiveness run.

### Fixed

- **One generated Python file could end an entire audit.** The Python collector
  parsed each module inside a `try` that caught `SyntaxError` and `ValueError`,
  but a pathologically wide or deep module is not invalid — it exhausts the
  parser instead. Measured on CPython 3.14: an `elif` chain overflows the pegen
  parser's fixed-size stack with `MemoryError` from 5,957 branches, and a chain
  of binary additions overflows AST construction with `RecursionError` from
  22,713 terms. Neither was caught, so one machine-generated file ended the run
  for every other file in the repository. Both are now recorded as modules that
  could not be turned into evidence, and the audit continues. The walk after the
  parse is guarded the same way and discards any partial analysis rather than
  keeping it, because a half-visited module reads as complete evidence of
  absence — a rule would report no seed where the seed sat in the unvisited
  subtree. Neither threshold is a constant worth pinning: the parser limit is a
  compile-time constant but the recursion limit is measured in stack bytes, and
  on CPython 3.10 the addition chain has no catchable range at all — it parses
  at 130,600 terms and takes the interpreter down at 130,650. The regression
  fixtures therefore size themselves, in a subprocess so a native crash cannot
  reach the test run, and fail loudly rather than passing vacuously if no size
  raises.

- **One raising rule from an installed pack discarded every other finding.** The
  engine called each rule's `evaluate` with no boundary, so a single exception
  propagated out of the check and took the other 77 results with it. A rule that
  is not one of adduce's own is now contained: it is recorded as `UNKNOWN` under
  its own identity, with a warning, and the run completes. `UNKNOWN` is
  applicable but unassessed, so containment lowers coverage instead of inventing
  a verdict, and moves neither the earned nor the possible score. A rule that
  returns something other than a `Finding`, or files one under another rule's id,
  is contained the same way — the latter would otherwise displace that rule's
  entry in the report, the score card and the baseline. adduce's own rules still
  fail loudly, because a built-in raising is a defect in adduce rather than a
  condition to degrade past, and membership is decided by class identity against
  the shipped set: a pack cannot join it, and neither a module name nor an
  equality or hash method of its own choosing will pass it off as a built-in.
  Text describing the failure is bounded and dropped unless it is a real
  identifier, since neither an exception's message nor its class name is under
  adduce's control. Containment now begins before evaluation. Each rule's id,
  category, title, weight and severity are read once, together, ahead of
  anything else, and a rule that is not one of adduce's own and cannot supply
  them is passed over: it produces no finding, it is counted as
  `rules.skipped_unidentifiable`, and the warning names its class because there
  is no id to name it by. A finding needs an id, a category and a title before
  it can appear in a report, a score or a baseline, so synthesising one would
  file a result under a rule that does not exist; degrading the rule instead
  would name it in the report and lower coverage, and neither is possible for a
  rule with no usable identity. A rule whose `applies_to` raises does have an
  identity, so it is contained the way a raising `evaluate` is: recorded
  `UNKNOWN` under its own id, applicable but unassessed, and counted as
  `rules.degraded`. It is not folded into the inapplicable count, because a
  rule that answered `False` leaves the score untouched and this one reached no
  answer, so counting it there would record an applicability decision the rule
  never made. A built-in that fails at either of the two new steps still ends
  the run. `rules.skipped_unidentifiable` is telemetry only and does not reach
  the score card.

- **File inventory order differed between Windows and POSIX, and reached
  rendered output.** The repository walk sorted the native `Path` objects it got
  back, and that comparison casefolds on Windows, so the same tree inventoried
  in a different order on a different platform while several reporters name only
  the first few paths they are given. Entries are now ordered by the segments of
  their relative POSIX path, which never casefolds and so is identical on every
  host. Measured over 49 targets — 33 synthetic cases, 15 pinned clones and a
  real clone of adduce itself: the previous ordering differs between the two
  platforms on 48 of them, and the new ordering is byte-identical to the
  previous POSIX ordering on all 49, so no recorded corpus output moves. A
  committed digest over a fixture that provokes the divergence pins the
  contract, so a platform that computes a different order fails the build.

- **SARIF anchored repository-level findings to a file that need not exist.** A
  finding with no source location was anchored to the literal path `README.md`
  whether or not the repository contained one. Measured: on a repository holding
  only `train.py`, 23 of 23 results pointed at a `README.md` that was not on
  disk, and on an empty repository all 9 did. The anchor is now chosen from the
  paths adduce actually inventoried and carries a line-1 region so a consumer
  can resolve it; a repository with nothing inventoried reports no location
  rather than a false one. A location that names its own file but never observed
  a line stays file-level, because a SARIF region is where a result was detected
  and line 1 would claim a detection that did not happen; a line below 1 is a
  collector bug and is now refused rather than rendered. Measured across eight
  pinned clones: 180 repository-level anchors gain a region, no file-scoped
  location gains one, and no existing line number moves. This changes the
  `adduceFindingKey` fingerprint for locationless findings in repositories with
  no root `README.md`, which re-keys any existing code-scanning alert for them —
  the old alert closes and an equivalent one opens.

- **A category adduce could not assess disappeared from the report entirely.**
  `scoring.py` treated "nothing assessed" as "nothing applicable": one line kept
  the category's weight out of the renormalisation, which is correct, and also
  removed the category from the score card, which is not. Because the drop happened
  before any reporter ran, terminal, Markdown, LaTeX and JSON lost it identically.
  A category whose findings are all `NOT_APPLICABLE` never applied and is still
  omitted; one holding an `UNKNOWN` applied and went unanswered, and is now kept
  with an empty score and its full findings list. Measured across the 33-case
  synthetic corpus: **13 categories in 10 cases** were being dropped this way and
  are now reported. `total`, `tier`, `coverage`, `evaluated_rules` and
  `considered_rules` are unmoved — the weight accumulation is never reached — and a
  before-and-after comparison over 34 repositories found **no change to any finding,
  status, or score**.
- **Terminal output claimed "all detected checks satisfied" for a category holding
  an unassessed check.** The note was built from `PARTIAL` and `FAIL` findings only,
  so a category holding `PASS` and `UNKNOWN` produced an empty note and fell through
  to that wording, which was false. It now reports how many checks could not be
  assessed, and a category with nothing assessed shows no score rather than `0/0`.


- **A repository with almost nothing in it could earn a respectable tier.** Most
  rules are assertions about code: given none, the ones that look for a problem
  find none and pass, and the ones that look for an artifact are satisfied by its
  bare presence. The weighted average of those passes was then presented as a
  tier. Measured: a directory containing eleven plausible-looking but meaningless
  files — a README with the right headings, pinned requirements nothing imports, a
  config no code reads, a Dockerfile, a results file produced by nothing — reached
  **72/100 and "Silver"** on ten lines of source. An **empty directory scored
  47.1/100**.

  A tier is now assigned only when the analyzer parsed at least
  `MINIMUM_ANALYSABLE_LINES` (100) physical lines of source; below that the tier
  reads `Unrated (insufficient evidence)`. The score itself is unchanged and still
  reported, because it is a real measurement of the checks that ran — what was
  wrong was calling it Silver. Measured across the corpus, the separation is not
  marginal: the largest synthetic fixture carries 15 lines and the smallest real
  repository 1,220, so every value in between behaves identically and the exact
  threshold is not load-bearing.

  This is a floor on whether anything can be said, **not a defence against
  deliberate gaming** — padding a file defeats it, and is meant to.

  Two repositories in the pilot corpus are now unrated, both correctly: an
  adversarial fixture, and `md-ml`, which contains no Python at all and previously
  scored 61.4/Bronze on source the analyzer never read.
- The scan inventoried gitignored files, so a repository containing a vendored or
  ignored working copy could earn passing rule statuses from files that are not
  part of its artifact. Measured on this repository, whose working tree carries
  fifteen gitignored clones: 30 rule statuses move — 9 rules stop applying, 13
  report not-applicable, 7 drop, and 1 improves. Nine of the thirty were
  reporting PASS on another repository's files, among them cuDNN determinism
  flags in a project that contains no CUDA code. The score reads 50.2 rather than
  60.5, which is the correction, not a regression. Scanning covers 344 files
  rather than 8,844, taking 1.2 s rather than 24.6 s at 65 MB rather than 435 MB
  of peak resident memory. Reproduce with `bench/runner.py finding-diff`.
- The remote collector asked whether each line's download carried a bound
  checksum before knowing whether the line contained a download at all, running
  an extra regular expression over every line of every `.py`, `.sh`, and
  `Makefile` in a repository. The probe is now consulted only for a line that
  carries a remote reference. Measured on the largest corpus repository, the
  collector goes from 3.155 s to 2.398 s, and both versions return exactly the
  same 6,683 references.

### Security

- The git queries that honour `.gitignore` now set `core.fsmonitor=false`, as the
  repository-metadata queries already did. Without it, git runs a
  repository-configured filesystem-monitor hook, which would let a scanned
  repository execute code inside an audit whose contract is that it never runs
  repository code. Every git invocation in the scan is now built by one helper so
  none can omit the guard. This was never present in a released version.

## [0.1.2] - 2026-08-04

### Validation status

Operational corpus validation is complete, and was carried out on the released
analyzer tree itself: 15/15 real repositories succeeded in two independent
runs, with 0 crashes, 0 timeouts, 0 contract failures, and 0 repository-byte
modifications. Comparing all 15 analyzer output artifacts pairwise under the
harness's own normalisation found no differing artifact; the runs were
deterministic apart from resource measurements — peak resident memory and
wall-clock runtime. Both runs record analyzer source tree `1b24ccf6…` with
`adduce_source_dirty: false` and version `0.1.2`, which is the tree released
here and the analyzer digest registered in the pilot preregistration. The
effectiveness pilot's two-independent-human-reviewer claim-review gate has not
been completed, so this release reports no false-positive rate, no score
separation, and no claim-link accuracy figure. Those numbers remain
developmental until that review gate closes.

### Added

- Added a security policy and threat model covering offline analysis, opt-in
  online resolution, plugins, provider use, and unsandboxed dynamic execution.
- Added CI enforcement for Ruff, progressive mypy checks, an 85% coverage
  floor, schema conformance, distribution metadata validation, and clean wheel
  and source-distribution installation smoke testing.
- Added end-to-end composite Action tests for successful scans, failing score
  gates, and report retention.
- Added a pre-registered real-repository candidate protocol with immutable
  harness inputs, clean-source attribution, independent claim review,
  role-separated finding review, deterministic repeat runs, and explicit
  separation of effectiveness and stress conclusions.
- Added reviewer conflict-of-interest declarations, assignment-bound recusals,
  independent adjudication, and non-personal reviewer identifiers to the
  corpus review contracts.
- Added direct boundary tests for cache handling, online resolution, plugin
  loading, the dynamic RNG import hook, generated-file writes, package version
  consistency, and versioning evidence.
- Added a tag-gated PyPI Trusted Publishing workflow with a protected
  environment boundary, stable-version consistency checks, immutable action
  pins, and an OIDC-only publish job.

### Changed

- Reduced the README to a landing page and moved its depth into `docs/`, which
  now carries a navigation index, the concepts and CLI references, CI recipes,
  the extension guide, the optional LLM layer, and the honest-limits list. The
  source distribution now ships the whole `docs/` tree, including the per-rule
  pages its index links to.
- Restricted online resolution to validated, globally reachable HTTPS
  destinations; bounded redirects, timeouts, response bodies, and cache
  entries; removed ambient proxy, cookie, and authorization-header use; and
  stopped accepting repository-supplied cache entries as network evidence.
- Clarified that dynamic repository copying provides input isolation only and
  is not a process, credential, filesystem, device, resource, or network
  sandbox.
- Bounded retained reproduction output and expected-output hashing, terminated
  timed-out POSIX process groups, and made reproduction reports resistant to
  symlink redirection and partial writes.
- Made fixed generated outputs use no-follow, atomic writes and fail closed on
  unsafe paths, stale bundle members, incomplete ledgers, and partial
  multi-format exports.
- Kept optional provider prose outside the evidence model, visibly marked it
  as unverified, and recorded provider, model, item, and fragment-hash
  provenance without retaining credentials or treating generated prose as
  repository truth.
- Kept ignored findings visible with their observed score, and made reviewer
  and artifact-chair modes bypass repository-supplied profiles, exclusions,
  suppressions, and thresholds unless the caller explicitly selects them.
- Narrowed checksum evidence to dataset-specific manifest, DVC, or
  download-bound signals; unlinked checksum files no longer imply that a data
  acquisition path verifies them.
- Made local tags and manifest commits precise checkout-attribution signals
  without presenting them as evidence of publication.

### Fixed

- Opened every file descriptor in the write boundary and the reproduction layer
  in binary mode on Windows. The C runtime's text-mode translation had expanded
  written newlines to CRLF, which failed the size check guarding each write,
  after which the boundary removed the file it had just created; no evidence
  ledger, manifest, checklist, appendix, or submission bundle could be written
  on that platform. On the read side the same translation stripped carriage
  returns, so a file already stored with CRLF endings was reported as changed
  and refused.
- Compared file change times only between stat sources that denote the same
  instant. The write boundary captured a file's identity with `lstat` and
  verified it through the opened descriptor with `fstat`; on Windows those
  fields hold different instants, so the comparison failed for every file
  modified after it was created and the snapshot refused a file that had not
  changed, producing spurious refusals in generation, the evidence ledger, and
  remote pinning.
- Propagated opt-in online resolution outcomes into rendered check reports and
  kept resolution failures as unknown evidence rather than proof of remote rot.
- Isolated plugin discovery failures and made rule and reporter ordering
  deterministic without allowing plugins to shadow built-ins.
- Improved Windows CI portability and made Action gating and SARIF reporting
  reliable when score thresholds are enabled.
- Replaced generated reproduction-success assumptions with author-reviewed
  tolerance and validation guidance.
- Made unfinished reproduction scaffolds fail closed and added explicit review
  markers to inferred Docker and README scaffold content.
- Removed invented release dates and versions from the `CITATION.cff`
  scaffold and marked repository-derived titles for author review.
- Prevented inferred and draft claim trails from being presented as fully
  supported before an author confirms the claim and its links.
- Restricted manifest authority for generated checklist and appendix answers
  to explicitly confirmed claims and rejected unsupported execution wording
  during generated-artifact audits.
- Prevented a framework import from making checkpoint-completeness checks pass
  without visible checkpoint payload evidence.
- Retained all manifest claims in claim-trail output and prevented sparse
  configuration or path matches from implying execution-backed support.

## [0.1.1] - 2026-07-12

### Added

- Evidence-backed generation safeguards, an auditable evidence ledger,
  strict-evidence mode, generated-artifact self-audits, and submission bundles.
- Support for papers outside the repository and a separate severity
  dimension for findings.
- A synthetic validation corpus, corpus tooling, and a scheduled PyPI
  installation smoke test.

### Changed

- Made static claims and generated answers more conservative, requiring direct
  evidence for affirmative checklist responses.
- Made manifest refreshes non-destructive and normalized repository paths
  consistently across platforms.

### Fixed

- Isolated dynamic verification runs and required comparable output or metric
  evidence instead of successful exit codes alone.
- Scoped result and configuration authority to author-linked claim evidence.
- Hardened generated metadata, secret handling, and GitHub Action behavior.

## [0.1.0] - 2026-07-04

Initial beta release of the offline-first research-artifact auditor.

### Added

- 78 checks across 17 categories, with confidence, locations,
  remediation, explainable scoring, venue profiles, and suppressions.
- The reproducibility manifest, claim-to-artifact trails, paper/code
  drift detection, result reconciliation, and reviewer-time estimates.
- Terminal, JSON, SARIF, Markdown, LaTeX, checklist, appendix, badge,
  archival metadata, and non-destructive scaffold outputs.
- Opt-in remote pinning and dynamic reproduction, kept separate from the
  offline static audit.
- Baseline regression checks, plugin entry points, a composite GitHub
  Action, a pre-commit hook, and validation-corpus tooling.
