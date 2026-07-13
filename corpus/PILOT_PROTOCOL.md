# Validation pilot protocol

Status: inventory frozen before the first Adduce run  
Freeze date: 2026-07-13  
Inventory: `corpus/repos.csv`  
Inventory SHA-256: `859fded20ca432fdd02a135b690ecaac75e5c2d457a2b7b2ea62dfc738107fd9`  
Human annotation status: pending

## Objective

This pilot is designed to find incorrect, inapplicable, low-value, and
unstable Adduce results before score calibration or broad adoption work. It
does not estimate a population-wide false-positive rate, validate the score
tiers, or predict artifact-evaluation outcomes.

The primary product question is claim-to-artifact traceability: when a
repository reports a result, does Adduce associate that claim with the correct
result, run, command, configuration, data, environment, seed, and commit?
Reviewed finding correctness, robustness, runtime, and deterministic
repeatability are supporting measurements.

## Three evidence layers

### Layer A: synthetic controls

The fourteen repositories under `corpus/synthetic/` isolate known positive,
negative, conflict, and generation-safety behaviours. Their versioned
expectations run in the normal test suite. Synthetic controls establish that a
specific detector behaves as designed; they do not establish performance on
unfamiliar repositories.

### Layer B: labelled real repositories

The pilot freezes ten claim-bearing repositories in two distinct strata.

- `badged_functional`: five machine-learning-related artifacts whose exact
  snapshots received Artifact Available, Artifact Functional, and Results
  Reproduced badges in the USENIX Security artifact-evaluation process. Badge
  evidence comes from the official [2023](https://secartifacts.github.io/usenixsec2023/results)
  and [2024](https://secartifacts.github.io/usenixsec2024/results) results. The
  exact evaluated-snapshot mapping and appendix source for each row are frozen
  in [`badged-provenance.csv`](badged-provenance.csv), together with the paper
  and result identity, complete badge set, artifact reference, immutable
  resolved commit, and UTC retrieval time.
- `unvetted`: five public research repositories selected without using formal
  artifact-evaluation status. They cover different ages, frameworks, domains,
  and repository shapes. “Unvetted” is a selection stratum, not a quality
  judgement.

The evaluated stratum is a strong external reference, not ground truth that
every Adduce rule must pass. It comes from one evaluation ecosystem and is not
representative of all high-quality machine-learning artifacts.

### Layer C: unlabelled stress repositories

Five repositories exercise operational limits: nanoGPT, minGPT, vit-pytorch,
Torchtune, and Transformers. The first three were retained from the original
stress seed before any pilot output was observed. Torchtune and Transformers
add framework-scale recipe, configuration, and file-count cases.

Stress results are excluded from effectiveness, score-separation, and
false-positive denominators. Only acquisition status, completion, crash,
timeout, runtime, deterministic repeatability, finding volume, and unsupported
repository structures are summarized.

## Selection and replacement policy

The inventory was frozen before inspecting pilot scores or findings.

- Every repository is identified by a canonical HTTPS URL and a full 40-hex
  commit.
- Layer B repositories must expose public source tied to research claims.
- Evaluated artifacts must have an independently documented Functional and
  Results Reproduced outcome at the pinned snapshot.
- Private, gated, duplicate, or irrecoverable snapshots are ineligible before
  the freeze.
- After the freeze, acquisition failures, Git LFS pointers, uninitialized
  submodules, crashes, timeouts, and unsupported layouts are recorded; a
  difficult result is never silently replaced.
- nanoGPT and minGPT share authorship and lineage and are not treated as
  independent observations.

This is a purposive pilot. Repository-level results and raw counts take
priority over pooled percentages because findings within one repository are
correlated.

## Methodological basis

The layer separation adapts benchmark discipline described by NIST's
[SAMATE program](https://www.nist.gov/itl/csd/secure-systems-and-applications/samate)
and [SATE V methodology](https://doi.org/10.6028/NIST.SP.500-326): known-defect
controls, analysis on real programs, manual interpretation, and operational
measurements answer different questions. This pilot reports only performance
on its frozen inventory. Badge terms follow the
[ACM artifact-review policy](https://www.acm.org/publications/policies/artifact-review-and-badging-current)
and provide snapshot provenance, not finding-level labels. These references
inform the design; they do not imply certification or conformance.

## Acquisition contract

Acquisition is the only network-dependent stage. `clone_repos.py` records a
versioned clone manifest containing the requested and resolved commits,
canonical origin, Git tree, submodule state, repository metadata hash, and
clean-worktree state. Existing clone manifests are never overwritten. A clone
whose origin, commit, or bytes change after acquisition is rejected before
scanning.

Local clones, raw outputs, labels, reports, snapshots, and derived analysis are
working data covered by `.gitignore`. The inventory, protocol, scripts, and
analysis code remain trackable public source.

## Execution contract

The pilot uses `run_validation.py` with a 300-second per-repository timeout.

- Only the 78 canonical built-in rules run. Installed third-party rule plugins
  are excluded.
- Scans are static and offline. A Python audit hook rejects socket and
  non-metadata subprocess events, write-capable file opens, and filesystem
  mutations in the scanner process. The child receives a minimal,
  credential-free environment. These are regression guards around the Python
  scanner, not an operating-system security sandbox.
- No repository installation, import hook, dynamic reproduction, online
  resolver, or repository command is invoked.
- Repository bytes are hashed before and after each scan. A write is a failed
  run, not an accepted side effect.
- Every input row produces one combined result row. Acquisition failures,
  crashes, timeouts, invalid JSON, and provenance mismatches are retained.
- A run records the exact Adduce source tree hash and Git state, rule IDs,
  Python/platform identity, dependency versions, inventory and clone-manifest
  hashes, the frozen claim-ground-truth hash, timestamps, timeout, raw-output
  hashes, resolved repository commits, logical CPU and available memory
  context, cache conditions, scanned file and byte counts, and
  platform-qualified peak resident set size when available.
- A new run directory is mandatory. `_RUN_SUCCESS` is written only after the
  complete output passes the run contract; incomplete or modified output is
  rejected by `validate_run.py`.

Two scans of the same clone set are compared after removing only run timing and
path metadata. Scores, categories, finding statuses, confidence, severity,
locations, messages, claim trails, reviewer-time estimates, and repository
commit identity must match exactly. Any unexplained difference is a
determinism defect. Runtime results remain machine-local operational
measurements; they are not compared as hardware-independent performance
benchmarks.

### Protocol amendment 1: scanner path preflight failure

Amended: 2026-07-13T23:07:34Z

The first attempted directory, `corpus/outputs/pilot-0.1.2dev0-a`, is retained
as an immutable failed run. Its run ID is
`0.1.2.dev0-9451ccacefb6-20260713T223159900427Z`; validation reports zero
successful repositories and 15 contract failures. The runner passed a
relative clone path while also changing the scanner child's working directory,
so the child resolved that path from inside the clone and its raw repository
identity did not contain the expected commit. The raw payload contract rejected
every result before any raw JSON entered the run evidence.

The correction resolves and verifies the clone path before launching the child
and has a regression test for relative runner arguments. The corrected,
independent pair is named `pilot-0.1.2dev0-r1-a` and
`pilot-0.1.2dev0-r1-b`. The failed preflight run is excluded from finding,
claim-link, score, and determinism analysis. The inventory, acquisition
records, claim ground truth, detector source, rule set, timeout, sampling
design, and acceptance rules are unchanged. No failed-run finding was used to
replace a repository, alter a claim label, tune a detector, or calibrate a
score.

### Protocol amendment 2: generation warning handling

Amended: 2026-07-13T23:14:52Z

The `r1-a` and `r1-b` scans are valid, contain 15 successful repositories
each, and have no deterministic output difference. Claim-link evaluation and
sampling completed against `r1-a`. The first bounded generation audit,
`corpus/reports/pilot-generation-audit-r1-a`, stopped after FRL and retains an
incomplete marker. SimCSE's static parse emitted a Python `SyntaxWarning` for
an invalid escape sequence; the analyzer child exited successfully, but the
generation wrapper rejected all non-empty stderr before accepting its output.
This was a wrapper failure, not a failed evidence-ledger judgement.

The generation child now sets `PYTHONWARNINGS=ignore::SyntaxWarning` and
records `ignore-syntaxwarning-only` in its audit policy. All other stderr
remains a hard failure, and a regression test fixes the exact warning policy.
Because this changes the immutable harness, the final comparable pair is
named `pilot-0.1.2dev0-r2-a` and `pilot-0.1.2dev0-r2-b`; the final reports,
samples, and generation audit target `r2-a`. The repository inventory,
acquisition records, frozen claim labels, analyzer source, built-in rule set,
timeout, selection design, and score remain unchanged. No finding review or
detector change informed this wrapper correction.

### Protocol clarification 3: claim-review status

Clarified: 2026-07-13T23:46:09Z

The r2-bound claim mapping was frozen before the first scan and passes all
mechanical source, checkout, coverage, and hash checks, but it has not yet
received documented review by human domain reviewers. It is retained unchanged
as a candidate annotation set and can support defect discovery, but it does not
support a claim-link accuracy estimate.

Formal acceptance requires two independent human domain reviewers to inspect
the exact frozen mapping without access to r2 claim-link output. Their review
record must bind the candidate file's SHA-256 and retain decisions, evidence,
timestamps, and reviewer identities. If review changes any claim or expected
link, create a new versioned truth file and a fresh run pair; do not rewrite the
r2 evidence. This clarification narrows permitted conclusions and changes no
inventory, scanner output, score, or frozen artifact.

## Ground truth and review

Before using the pilot for detector changes:

1. After acquisition and before the first scan, map one headline claim in each
   Layer B repository to the expected code, reported result, run, output,
   command, configuration, data, environment, seed, and commit. Use `unknown`
   or `not_applicable` rather than inventing a link. Validate the local records
   with `claim_ground_truth.py` and retain their SHA-256. No claim ground-truth
   records are pre-populated by the project. If a labelled snapshot cannot be
   acquired, bind an explicit unavailable record to the failed clone-manifest
   entry; do not invent a claim source or remove the repository. A distinct
   human domain reviewer independently verifies every frozen record. Future
   and confirmatory truth sets complete that review before scanning; the
   retained r2 candidate follows the blinded exception recorded in protocol
   clarification 3. Automated checks may validate structure and source
   identity, but they are not reviewer decisions.
2. Draw an all-status, suppressed-inclusive census and deep-review every
   finding for three predeclared sentinel cases: FRL, SimCSE, and Torchtune.
3. Draw a fixed, seeded, status-and-category-stratified sample from the other
   completed Layer B repositories. Include pass, partial, fail, unknown, and
   not-applicable results so false passes and incorrect abstentions remain
   detectable.
4. Run `audit_sentinel_generation.py generate` and its independent `validate`
   mode for exactly FRL, SimCSE, and Torchtune. Retain the strict NeurIPS
   checklist, artifact appendix, and complete ledger for each repository plus
   the audit manifest. Both commands must exit 0: every ledger-classified
   `yes` or `partial` requires evidence, a `yes` must meet the strict evidence
   threshold, and static text must not imply execution.

Finding review records separate judgements rather than collapsing them into
one label. Reviewers follow the operational definitions in the
[`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md):

- correctness: `correct`, `incorrect`, or `unclear`;
- applicability: `applicable`, `not_applicable`, or `unclear`;
- utility: `actionable`, `minor`, `low_value`, `not_applicable`, or `unclear`;
- root cause and verification mode;
- reviewer identity, time, confidence, notes, and evidence links.

The sample binds every finding to the completed run metadata, combined results,
and exact repository raw JSON by SHA-256. A common sample-set record also binds
the immutable-run sampler source hash, sampler Python implementation and
version, exact arguments and suppression policy, eligible and selected
repository IDs, entry count, and canonical finding-fingerprint set.
Review, adjudication, and reporting reconstruct the selection from the run and
reject v1 or mixed samples, changed run identity, deleted or injected records,
inconsistent bindings, repository commit drift, finding drift, or sampler
drift. Sample and review files remain outside the immutable run directory.

The first 100–200 reviewed findings should receive at least 20% independent
second review. Reviewer records remain separate, disagreements receive an
explicit adjudication record, and agreement counts accompany every descriptive
aggregate. The review command hides cohort and other reviewers' judgements by
default. Unweighted reviewed-sample proportions are not corpus rates.

Every review and adjudication cites evidence. `unclear` decisions explain what
could not be resolved, and an adjudicator is independent of the original
reviewers. A claim trail cannot be accepted as `supported` if any expected
claim-to-artifact link is known to be incorrect, even when all other links are
present.

## Decision rule

Pilot evidence is used in this order:

1. Correct factually wrong extraction or semantic equivalence.
2. Change unresolved evidence from absence/failure to `unknown` where needed.
3. Correct applicability and context.
4. Improve misleading wording.
5. Consider severity, weight, or tier changes only after the preceding defects
   are removed and a larger labelled corpus supports calibration.

At most three general detector problems should enter the first correction
cycle. Each correction requires a focused synthetic control, a same-commit
before/after corpus comparison, and review of any generated-answer upgrade.
Once findings from this pilot inform a detector change, the pilot is a
development set. Its before/after measurements are paired diagnostics, not an
unbiased accuracy estimate. Corpus expansion must freeze a separate
confirmatory holdout before its results are inspected; publication or any
generalized performance claim depends on that holdout rather than the reused
pilot.

## Reporting limits

The pilot report publishes counts, repository-level distributions, machine-local
runtime and resource observations, crash/timeout outcomes, reviewer agreement,
and carefully scoped rule-level observations. With five repositories per
labelled stratum, all comparisons are exploratory. No score threshold, badge
prediction, calibrated tier, cross-machine performance comparison, or
population false-positive claim follows from this pilot alone.
