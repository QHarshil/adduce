# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
