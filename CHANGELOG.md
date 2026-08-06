# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Validation status

The `r6` effectiveness preregistration lock is void, deliberately and once. Its
analyzer digest binds the `src/adduce` byte tree, and the work in this release
changes that tree. Invalidating it now costs nothing, because no human reviewer
decision has been collected; the same change after the claim review begins would
cost 220 decisions. Every other frozen input is unchanged and still verified: the
preregistration schema, the built-in rule-ID set, the corpus inventory, and the
frozen claim truth `9a26d06c…`. A successor lock will be registered under a dated
protocol amendment against the finished analyzer, and no effectiveness,
calibration, or false-positive figure is stated in the meantime.

Two analysis-plan files changed. `corpus/scripts/check_builtin.py` permits the
two read-only git queries that honouring `.gitignore` requires; its offline
enforcement is otherwise unchanged, and the queries are measured to be a no-op on
the pilot corpus — all fifteen pinned clones report zero ignored paths, so no
finding, score, or status moves for any of them. `corpus/scripts/run_contract.py`
stops recomputing a tier from a score, which is no longer a valid invariant now
that a tier is withheld when the analyzer parsed too little source; it validates
the new `evidence_base` block instead. Both changes are recorded against the
digests the lock still carries, and the record asserts that exactly those two
files moved.

### Added

- Added per-stage timing and work counters to the check pipeline, reported by
  `adduce check --timings` and as a `telemetry` block in the JSON report. A
  default offline run records 23 stage durations and 8 counters. Durations differ
  between identical runs, so they are omitted unless requested and the default
  report remains byte-for-byte stable. Measured cost on the largest corpus
  repository, per operation rather than by subtracting two whole-run timings:
  11.6 µs for the stage timers, 1.1 ms for counter recording over 8,840
  inventoried files, and 3.3 µs for a snapshot — about 0.005% of a 21-second run.
- Added a benchmark harness under `bench/` with a committed baseline covering
  five real repositories, this repository, and the fourteen synthetic
  positive-control repositories. It records cold runtime, repeat runtime, peak
  resident memory with its platform's unit, files and lines per second, per-stage
  timings, disk reads per inventoried file, parser failures, and whether a
  repeated run renders byte-identically. A target that is absent or fails to
  measure is recorded with the reason rather than defaulted. `bench/runner.py
  compare` fails on a regression and now gates CI.
- Added `bench/runner.py finding-diff`, which enumerates every rule status that
  honouring `.gitignore` moves, and classifies each move as a rule that stopped
  applying, became not-applicable, dropped, or improved. It exists so the
  behaviour change below is reproducible rather than asserted.

### Changed

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
  `evaluated_rules`, `considered_rules`, `coverage_percent`, and
  `analysable_lines`, so a consumer can see how much the score rests on rather
  than inferring it. Existing keys are unchanged. `ScoreCard` gains the matching
  fields and a `coverage` property, all defaulted, and `score()` takes an
  optional `analysable_lines` keyword — a caller that omits it still gets a tier,
  so plugins scoring findings directly are unaffected.
- `corpus/scripts/run_contract.py` no longer recomputes a tier from a score
  unconditionally. That invariant held only while a tier was a pure function of
  the score, and enforcing it would now reject a correct artifact for any
  repository the analyzer could not read. It validates the `evidence_base` block
  and checks the scored population exactly instead.

### Fixed

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
