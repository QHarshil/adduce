# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Improved Windows CI portability and made Action gating and SARIF reporting
  reliable when score thresholds are enabled.
- Replaced generated reproduction-success assumptions with author-reviewed
  tolerance and validation guidance.

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
