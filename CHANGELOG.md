# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Generation safety: the contract (docs/generation-safety.md), the evidence
  ledger (`.adduce/evidence-ledger.json`) with answer levels, evidence
  strengths, search scope, and generation provenance; `--strict-evidence` on
  `checklist` and `appendix`; visible `[EVIDENCE: path:line]` anchors and
  `[AUTHOR REVIEW REQUIRED]` markers; a safety summary after every
  generation command; `adduce audit-generated` (R-GEN-001..005); and
  `adduce package` for a one-command submission bundle.
- Synthetic positive-control corpus: 14 planted-fault repositories with
  pinned expectations, run as a permanent regression suite, including
  red-team cases whose correct answer is not a confident one.
- Corpus tooling: clone/run/summarize/sample/label scripts implementing the
  three-layer validation protocol (synthetic controls, labelled cohorts,
  unlabelled stress corpus).
- `--paper` on `check`, `drift`, `manifest`, `checklist`, and `appendix` for
  papers kept outside the code repository, plus an explicit repository-only
  notice when no paper sources are detected.
- A severity dimension on every rule and finding, separate from score
  weight (a committed secret is high severity at modest weight).
- Installation smoke test against the published PyPI package
  (scripts/smoke_install.sh, run weekly in CI).

### Changed

- Repository-relative paths are normalised to POSIX form on every platform;
  fixes Windows path-separator failures and prefix checks.
- R-RES-003 no longer accepts paper prose alone as multi-run evidence: a
  stated seed count must be corroborated by manifest seeds or a seed sweep
  in run scripts, otherwise it drafts as partial.

## [0.1.0] - 2026-07-04

First release: a local research-artifact auditor. Offline by default;
online resolution and dynamic verification are separate, opt-in commands.

### The static audit

- `adduce check`: 78 rules across 17 categories — code & execution,
  environment, dependencies, data, documentation, determinism, numerical
  precision & hardware, paper & artifact consistency (drift), result
  reconciliation, run traceability, checkpoint state, notebooks,
  portability, remote artifacts & rot, versioning, access & legal, and
  archival readiness. Every finding carries status, confidence, locations,
  and a remediation; framework gating keeps inapplicable rules out of the
  score in both directions.
- Layered determinism analysis (per-library seeds, cuDNN flags, strict
  controls, both DataLoader RNG sources, sklearn `random_state`), resolved
  through an import-alias map plus one-hop wrapper resolution.
- Evidence collectors for Python AST, configs (YAML/JSON/TOML/Hydra/
  DeepSpeed), LaTeX sources (comment stripping, scientific/LaTeX-math
  notation, table parsing), notebooks, dependency manifests, data
  provenance, remote-artifact calls, precision controls, result files
  (CSV/JSON/JSONL, TensorBoard/W&B/MLflow presence), run history (shell,
  Makefile, SLURM, Hydra outputs, W&B/MLflow metadata), portability, and git.

### Claim traceability

- The Reproducibility Manifest (`.adduce/manifest.yaml`, `adduce manifest`):
  claims, datasets, remotes, environment, and a smoke target; auto-drafted
  from evidence, authoritative once confirmed.
- The claim-to-artifact graph: per-claim trails (metric → command → config →
  data → env → seeds → commit) with per-edge resolution status, printed in
  `adduce check` and exported in JSON.
- Paper↔code drift detection with authority ranking (materialised run config
  over checked-in config over defaults) and rounding-aware comparison;
  result reconciliation against local logs.
- Reviewer time-to-first-result estimation with named cost factors, and
  three report framings: `--mode author|reviewer|ae-chair` (the last with
  ACM badge-eligibility assessment; execution-based badges never claimed).

### Deliverables

- `adduce checklist` (NeurIPS, ACL) and `adduce appendix` (ACM Artifact
  Appendix), drafted from evidence with author-input items marked.
- `adduce export`: RO-Crate, Croissant (per dataset), CodeMeta,
  `.zenodo.json`, `checksums.txt`, and a Software Heritage note;
  `adduce archive-plan` for the deposit steps.
- Reports: Rich terminal, JSON, SARIF 2.1.0, Markdown, LaTeX appendix, and
  a badge as shields.io endpoint JSON or self-contained SVG.
- Scaffolds (`adduce fix`): seed utilities, Dockerfile, CITATION.cff,
  reproduce.sh, README sections — all non-destructive.

### Fenced, opt-in layers

- `adduce pin-remotes`: offline detection of floating Hugging Face /
  torch.hub / raw-URL references; opt-in online resolution of current SHAs
  (cached in `.adduce/cache`) and diff-gated libcst codemods that add
  `revision=` pins, with the forward-guarantee caveat stated.
- `adduce reproduce`: runs the manifest smoke target twice with a pinned
  seed, fingerprints outputs and stdout metrics, and asserts agreement;
  requires `--yes` and is never invoked by `check`. Plus a first-use RNG
  ordering diagnostic (`python -m adduce.dynamic.import_hook`).
- Optional BYO-key LLM layer (OpenAI/Anthropic/Ollama) for checklist
  justification prose only; checks and scoring stay deterministic.

### Adoption machinery

- Explainable category-weighted scoring with venue profiles (`default`,
  `neurips`, `iclr`, `acl`, `acm`, `strict`) and custom TOMLs.
- `adduce baseline` + `--fail-on-regression` ratchet; diagnostic-by-default
  CI posture; `adduce diff` artifact-regression mode.
- Inline suppression (`# adduce: ignore=R-XXX-000`) and `[tool.adduce]`
  configuration; `--only`/`--skip` rule filtering.
- Plugin entry points for rules (`adduce.rules`) and reporters
  (`adduce.reporters`); composite GitHub Action and pre-commit hook.
- Validation-corpus harness (`corpus/run_validation.py`) implementing the
  two-cohort protocol with honest measurement rules.
