# Validation corpus

Adduce uses three separate evidence layers because the questions they answer
are distinct: detector regression; reviewed decision correctness and
claim-link behaviour; and operational robustness.
The [pilot protocol](PILOT_PROTOCOL.md) freezes the initial inventory,
selection rules, execution contract, review method, and reporting limits before
any pilot output is inspected.

## Layer A: synthetic controls

Fourteen small repositories under `corpus/synthetic/` isolate known positive,
negative, conflict, and generation-safety behaviours. The expectations in
`corpus/synthetic/expectations.yaml` run with the normal test suite:

```bash
pytest tests/test_synthetic_corpus.py
```

These controls prove only that the specified cases remain stable. They do not
measure reviewed decision correctness on unfamiliar repositories or a
population false-positive rate.

## Layer B: labelled real repositories

The frozen pilot contains ten claim-bearing repositories:

- five `badged_functional` snapshots with independently documented Artifact
  Functional and Results Reproduced outcomes. The exact snapshot-to-evaluation
  mapping is recorded in [`badged-provenance.csv`](badged-provenance.csv),
  including the result identifier, full badge set, artifact reference, resolved
  commit, and UTC retrieval time;
- five heterogeneous `unvetted` research repositories selected without using
  formal artifact-evaluation status.

“Unvetted” is a sampling stratum, not a negative quality label. Badge types
remain separate metadata and are never treated as ground truth that every rule
must pass.

Layer B supports manual finding review, claim-to-artifact link review, and
descriptive score analysis. The five-repository strata are exploratory and do
not support calibrated tiers or generalized performance claims.

## Layer C: unlabelled stress repositories

Five pinned repositories test file-count, framework, recipe, and configuration
limits. Layer C supports only operational measurements: acquisition,
completion, crash/timeout, runtime, deterministic repeatability, finding
volume, and unsupported structures. Stress scores do not enter effectiveness
or calibration claims.

## Local evidence and public source

The inventory, protocol, scripts, schemas, and synthetic controls are public
source. Clones, raw runs, labels, reports, snapshots, and derived analysis are
local working data covered by the public `.gitignore`.

Acquisition requires Git and network access to retrieve the frozen commits.
After acquisition, the scan and review workflow is local and adds no
dependencies beyond Adduce itself. It does not install or import audited
repositories, invoke repository commands, resolve network resources, or
enable third-party Adduce plugins.

## Acquire the frozen inventory

This is the only network-dependent stage. Use a new clone directory. The clone
script refuses to overwrite an existing manifest and records exact commits,
origin URLs, Git trees, submodule state, the inventory hash, and
clean-worktree state.

```bash
python corpus/scripts/clone_repos.py \
  --repos corpus/repos.csv \
  --out corpus/clones/pilot-2026-07-13
```

After the inventory is frozen, an acquisition failure remains a recorded
failure. Do not silently replace a repository after seeing a result.

## Freeze claim ground truth before scanning

Claim ground truth is local review data, not a pre-populated project claim.
After acquisition and before the first Adduce scan, record one headline claim
for every Layer B repository against the published
[`claim-ground-truth.schema.json`](claim-ground-truth.schema.json). Each record
pins the exact source quote and file or paper snapshot, the repository commit,
and the expected resolution of code, reported result, run, output, command,
configuration, data, environment, seed, and commit links. Unknown and
not-applicable relationships remain explicit.

If a Layer B snapshot cannot be acquired, record it in
`unavailable_repositories` exactly as it appears in the clone manifest. This
keeps the acquisition failure in scope while marking the claim as not
evaluable; it is never replaced or fabricated. The ground-truth file also
binds the clone-manifest SHA-256.

```bash
python corpus/scripts/claim_ground_truth.py validate \
  --claims corpus/labels/pilot-claims.json \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13
```

Validation checks source and artifact hashes, exact README line ranges, the
checked-out commit, inventory coverage, declared reviewer identity and
timestamps, and the presence of every expected link target. These mechanical
checks do not substitute for human domain review. Paper claims require a hashed
local paper snapshot plus a page or exact locator. The command prints the
frozen ground-truth SHA-256. An effectiveness run copies this exact file and its
accepted claim-review artifact into immutable run evidence and records both
digests.

Before the first candidate scan, initialize the separate human-review artifact
that is defined by [`claim-review.schema.json`](claim-review.schema.json). The
command below writes an empty scaffold; it does not create, infer, or
pre-populate a human decision:

```bash
CLAIM_REVIEW_A=corpus/labels/pilot-claim-review-r6-reviewer-a.json
CLAIM_REVIEW_B=corpus/labels/pilot-claim-review-r6-reviewer-b.json
CLAIM_REVIEW=corpus/labels/pilot-claim-review-r6-merged.json

python corpus/scripts/claim_review.py init \
  --claims corpus/labels/pilot-claims.json \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --candidate-run pilot-0.1.2-r6-a \
  --candidate-run pilot-0.1.2-r6-b \
  --out "$CLAIM_REVIEW_A"

cp "$CLAIM_REVIEW_A" "$CLAIM_REVIEW_B"
```

A neutral coordinator creates the two identical empty files before review and
gives each reviewer access only to their assigned copy. Each human domain
reviewer records exactly one claim decision and one decision for each of the ten
expected links, with evidence, expertise, identity, timestamp, and affirmative
blinding and conflict-of-interest declarations. The conflict declaration is
scoped to the assigned repository and claim identifier and must affirm no
relevant authorship or contribution; close collaboration, supervision, or
employment; financial conflict; or personal conflict. A reviewer who cannot
make every affirmation is recused and the assignment is given to a different
reviewer; a disclosure does not make the reviewer eligible. Neither reviewer
may see the other file or any Adduce
claim-link output for the bound truth, including retained r2 evaluations,
before both files are returned and locked. The coordinator then merges them
deterministically:

```bash
python corpus/scripts/claim_review.py merge \
  --review "$CLAIM_REVIEW_A" \
  --review "$CLAIM_REVIEW_B" \
  --claims corpus/labels/pilot-claims.json \
  --out "$CLAIM_REVIEW"
```

The merge records both source-file SHA-256 values and reviewer identities and
never adds an adjudication. An independent adjudicator resolves every decision
disagreement in the merged file. The adjudicator makes the same assignment-
scoped conflict declaration after the initial reviews and before recording a
decision; a conflicted adjudication is reassigned. Before `r6-a` starts, the
completed artifact and its two immutable sources must pass:

```bash
python corpus/scripts/claim_review.py validate \
  --review "$CLAIM_REVIEW" \
  --claims corpus/labels/pilot-claims.json \
  --initial-review "$CLAIM_REVIEW_A" \
  --initial-review "$CLAIM_REVIEW_B" \
  --require-accepted
```

`--require-accepted` fails unless both reviews exist for every claim and link,
the merged source hashes reproduce the independent decisions, all disagreements
are adjudicated, and the resolved decisions accept the exact truth file. If
review requires a truth change, freeze a new version and add a dated protocol
amendment with new candidate-pair names; never rewrite r2.

## Run twice and validate

Use fresh output directories. Each repository gets 300 seconds in the pilot.
The scanner runs only canonical built-in rules, installs a Python audit guard
against socket and non-metadata subprocess activity, and hashes repository
bytes before and after each check. The audit guard detects scanner regressions;
it is not an operating-system sandbox.

The [2026-07-31 protocol amendment](PILOT_PROTOCOL.md#protocol-amendment-7-post-platform-fix-re-lock-and-candidate-rename)
pre-registers the fresh pair below. These commands are prospective: the pair
must not run until the claim-review gate above passes and the candidate source
and harness have stabilized. Effectiveness runs also require Adduce source at a
full, clean Git commit so the analyzer can be reconstructed; a release tag or
package publication is not required for this candidate evidence.

The tracked
[`pilot-r6-preregistration.json`](pilot-r6-preregistration.json) is the
machine-readable prospective lock. It freezes the exact candidate names,
300-second timeout, analyzer, rule-set and dependency identity, repository,
clone, truth and provenance digests, reviewer/offline/no-plugin execution
policy, and the complete analysis-plan file map. Before creating an output
directory, effectiveness preflight requires the analyzer, preregistration, and
every required harness file to be tracked and clean at one Git `HEAD`.

`pilot-r3-preregistration.json` is also present because protocol amendment 4
names it. Protocol amendment 5 voided that lock, and it is retained only as a
historical artifact: no script, test, or run loads it.
`pilot-r4-preregistration.json` is also present because protocol amendment 5
names it. Protocol amendment 6 voids that lock, and it is retained only as a
historical artifact: no script, test, or run loads it.
`pilot-r5-preregistration.json` is also present because protocol amendment 6
names it. Protocol amendment 7 voids that lock, and it is retained only as a
historical artifact: no script, test, or run loads it, and
`pilot-r6-preregistration.json` is the live prospective lock.

```bash
python corpus/scripts/run_validation.py \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --claims corpus/labels/pilot-claims.json \
  --claim-review "$CLAIM_REVIEW" \
  --claim-review-source "$CLAIM_REVIEW_A" \
  --claim-review-source "$CLAIM_REVIEW_B" \
  --out corpus/outputs/pilot-0.1.2-r6-a \
  --timeout 300

python corpus/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2-r6-a

python corpus/scripts/run_validation.py \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --claims corpus/labels/pilot-claims.json \
  --claim-review "$CLAIM_REVIEW" \
  --claim-review-source "$CLAIM_REVIEW_A" \
  --claim-review-source "$CLAIM_REVIEW_B" \
  --out corpus/outputs/pilot-0.1.2-r6-b \
  --timeout 300

python corpus/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2-r6-b

python corpus/scripts/compare_runs.py \
  corpus/outputs/pilot-0.1.2-r6-a \
  corpus/outputs/pilot-0.1.2-r6-b \
  --out corpus/reports/pilot-determinism-r6.json

python corpus/scripts/claim_review.py validate \
  --review "$CLAIM_REVIEW" \
  --claims corpus/labels/pilot-claims.json \
  --initial-review "$CLAIM_REVIEW_A" \
  --initial-review "$CLAIM_REVIEW_B" \
  --require-accepted \
  --run corpus/outputs/pilot-0.1.2-r6-a \
  --run corpus/outputs/pilot-0.1.2-r6-b
```

A run directory is never reused. `_RUNNING` marks interrupted output;
`_RUN_SUCCESS` appears only after metadata, row counts, raw-file sets,
repository commits, Adduce versions, and SHA-256 records agree. Sampling and
reporting reject incomplete or modified runs. Runtime measurements are
machine-local operational observations, not cross-machine benchmarks. Each
run records logical CPU and available physical-memory context, the fact that
filesystem caches were not cleared, the disabled Adduce application-cache
path, per-repository scanned file and byte counts, and platform-qualified peak
resident set size when the standard library exposes it.

For an effectiveness run, the runner validates the accepted human review, its
truth digest, candidate label, and completion timestamps before it creates the
output directory. It also reconstructs the merge from the two independently
completed source files. The immutable run manifest binds byte-for-byte copies
of the truth, merged review, and both review sources. `--operational-only` runs
contain none of those artifacts, may use a dirty development tree, and cannot
support effectiveness conclusions.

The initial directory `corpus/outputs/pilot-0.1.2dev0-a` is a retained
preflight failure: all 15 scanner payloads were rejected because a relative
clone argument was resolved again after the child working directory changed.
It contains no accepted raw results and is excluded from effectiveness and
repeatability analysis. The absolute-path correction produced a valid,
deterministic `r1-a`/`r1-b` pair. A subsequent generation-audit preflight
stopped on a repository `SyntaxWarning`; the narrowly amended r2 harness then
produced a valid deterministic pair and generation audit. All preflights, r1,
and r2 remain immutable historical evidence. Current scripts have changed and
must not reinterpret any of those directories; historical verification uses
only each run's frozen `harness/` copy. The preregistered r6 pair is the next
candidate and has not been run as part of this documentation change.

The following historical checks are read-only. They invoke only the frozen r2
harness, do not pass an output path, and disable bytecode writes so validation
does not add `__pycache__` entries to retained evidence:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B \
  corpus/outputs/pilot-0.1.2dev0-r2-a/harness/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2dev0-r2-a
PYTHONDONTWRITEBYTECODE=1 python -B \
  corpus/outputs/pilot-0.1.2dev0-r2-b/harness/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2dev0-r2-b
PYTHONDONTWRITEBYTECODE=1 python -B \
  corpus/outputs/pilot-0.1.2dev0-r2-a/harness/scripts/compare_runs.py \
  corpus/outputs/pilot-0.1.2dev0-r2-a \
  corpus/outputs/pilot-0.1.2dev0-r2-b
```

Do not use current corpus scripts, add `--out`, or otherwise write into either
historical run directory.

## Produce a descriptive report

Reports are written outside the immutable run directory.

```bash
python corpus/scripts/summarize.py \
  --run corpus/outputs/pilot-0.1.2-r6-a \
  --out corpus/reports/pilot-summary-r6.md
```

The report keeps evaluated, unvetted, and stress roles distinct. A frequent
fail/partial rule is described as chatty until manual review establishes
whether it is noisy.

## Draw and review a finding sample

Use the operational definitions and edge-case rules in the
[`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md) for every review. The retained r2
samples and outputs remain immutable historical evidence. Because
`label_findings.py` is part of the run-bound harness, the current reporter must
not reinterpret those samples after its source changes. Draw new bound samples
only from the validated r6 candidate. Do not point any current command at r2:

```bash
RUN=corpus/outputs/pilot-0.1.2-r6-a
SENTINELS=corpus/labels/pilot-sentinels-r6.jsonl
SAMPLE=corpus/labels/pilot-layer-b-sample-r6.jsonl
ALLOCATION=corpus/labels/pilot-review-allocation-r6.json
PRIMARY_REVIEW=corpus/labels/pilot-findings-r6-primary.json
SECONDARY_REVIEW=corpus/labels/pilot-findings-r6-secondary.json
MERGED_REVIEW=corpus/labels/pilot-findings-r6-merged.json
```

First draw a complete census of all findings for the predeclared FRL, SimCSE,
and Torchtune sentinels:

```bash
python corpus/scripts/sample_findings.py \
  --run "$RUN" \
  --census \
  --include-repo frl \
  --include-repo simcse \
  --include-repo torchtune \
  --statuses pass,partial,fail,unknown,not-applicable \
  --seed 0 \
  --out "$SENTINELS"
```

The census includes suppressed findings by default. Do not use
`--exclude-suppressed` for the primary pilot; it exists only for a separately
declared sensitivity analysis. The historical r2 census contains 156 Layer B
findings from FRL and SimCSE and 78 Layer C findings from Torchtune. Torchtune
remains a separate stress diagnostic even though the three sentinels share one
bound review file. Record the fresh sample's actual counts rather than assuming
they are unchanged.

Then sample all statuses from the remaining Layer B repositories so false
passes, applicability errors, and inappropriate abstentions can be detected.
Finding sampling excludes the stress cohort by default. The command below also
excludes the two Layer B sentinels already included in the census. The seed
makes repository and status/category-stratum selection repeatable.

```bash
python corpus/scripts/sample_findings.py \
  --run "$RUN" \
  --n-repos 8 \
  --exclude-repo frl \
  --exclude-repo simcse \
  --per-stratum 2 \
  --statuses pass,partial,fail,unknown,not-applicable \
  --seed 0 \
  --out "$SAMPLE"
```

Before annotation begins, freeze one allocation across both review files:

```bash
python corpus/scripts/review_allocation.py create \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --seed 0 \
  --calibration-count 40 \
  --out "$ALLOCATION"

python corpus/scripts/review_allocation.py validate \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE"
```

The manifest follows the published
[`review-allocation.schema.json`](review-allocation.schema.json). It records the
candidate run and harness identity, selector and schema SHA-256, seed, both
pristine sample-source SHA-256 values, immutable source projections, sample-set
hashes, exact finding fingerprints, and repository/status strata. It excludes
stress records even though the sentinel source is mixed. The 40 calibration
findings are balanced across repositories and available emitted
(`fail`/`partial`), pass, and abstention (`unknown`/`not-applicable`) strata.
The second-review allowlist contains the calibration set and is exactly the
larger of 40 or 20% of every fresh Layer B review target, rounded up. It is
therefore a quota over the full Layer B workload, not the first 100–200 records,
and calibration is included in rather than added to that quota. Final
validation separately requires at least one review for every Layer B target.

Create two role-bound reviewer files from the pristine samples. The primary
file contains every Layer B target. The secondary file contains exactly the
persisted second-review allocation. Neither file contains stress records,
cohort assignments, or another reviewer’s decisions. Give each reviewer only
their own file:

```bash
python corpus/scripts/label_findings.py init-review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review-role primary \
  --reviewer-id reviewer-1 \
  --out "$PRIMARY_REVIEW"

python corpus/scripts/label_findings.py init-review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review-role secondary \
  --reviewer-id reviewer-2 \
  --out "$SECONDARY_REVIEW"
```

Each reviewer first completes only the calibration assignment in their own
file. The command records a domain-expertise statement and requires a
time-stamped declaration that the review is independent, the other decisions
were not seen, and the other reviewer file was not accessed. Before the first
decision, it also requires a time-stamped conflict-of-interest declaration
bound to the exact assigned repository set and finding-fingerprint-set digest.
The declaration affirms no relevant authorship or contribution; close
collaboration, supervision, or employment; financial conflict; or personal
conflict. If any affirmation is unavailable, stop and reassign the complete
role-bound reviewer file. Do not record a person's name, employer,
relationship, or reason for recusal in the review artifact.

```bash
python corpus/scripts/label_findings.py review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$PRIMARY_REVIEW" \
  --review-set calibration

python corpus/scripts/label_findings.py review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$SECONDARY_REVIEW" \
  --review-set calibration

python corpus/scripts/label_findings.py validate-calibration \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$PRIMARY_REVIEW" \
  --review "$SECONDARY_REVIEW"
```

Continue only after correctness and applicability each reach at least 80%
exact agreement and the handbook resolves any repeated ambiguity. The
calibration validator prints both exact reviewer-file SHA-256 values so the
neutral coordinator can retain byte-for-byte, read-only checkpoint copies
without combining or disclosing decisions.
Cohen’s kappa remains descriptive, not the sole gate. If the gate fails,
version the handbook and freeze a new allocation under a dated amendment before
restarting. The 40 items are calibration work, not an effectiveness estimate.

After calibration passes, each reviewer completes the remaining records in
their own file. The primary reviewer covers every Layer B target; the secondary
reviewer covers only the predeclared quota.

```bash
python corpus/scripts/label_findings.py review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$PRIMARY_REVIEW" \
  --review-set remaining

python corpus/scripts/label_findings.py review-source \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$SECONDARY_REVIEW" \
  --review-set remaining
```

Only after both files are complete may the coordinator merge them. The merged
artifact records the exact source-file SHA-256, reviewer role, identity,
expertise statement, blinding declaration, and conflict-of-interest
declaration. Input order does not affect the result. Initial decisions cannot
be changed during merge.

```bash
python corpus/scripts/label_findings.py merge-review-sources \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$PRIMARY_REVIEW" \
  --review "$SECONDARY_REVIEW" \
  --out "$MERGED_REVIEW"

python corpus/scripts/label_findings.py adjudicate-review \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$MERGED_REVIEW" \
  --initial-review "$PRIMARY_REVIEW" \
  --initial-review "$SECONDARY_REVIEW" \
  --adjudicator-id adjudicator-1

python corpus/scripts/label_findings.py validate-review \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$MERGED_REVIEW" \
  --initial-review "$PRIMARY_REVIEW" \
  --initial-review "$SECONDARY_REVIEW" \
  --require-complete

python corpus/scripts/label_findings.py report-review \
  --allocation "$ALLOCATION" \
  --run "$RUN" \
  --sample "$SENTINELS" \
  --sample "$SAMPLE" \
  --review "$MERGED_REVIEW" \
  --initial-review "$PRIMARY_REVIEW" \
  --initial-review "$SECONDARY_REVIEW"
```

The merged effectiveness report excludes stress by construction. Review the
Torchtune census separately as a diagnostic-only Layer C artifact; do not merge
its decisions into effectiveness proportions.

Each adjudication also carries a declaration bound to the disputed repository
and finding fingerprint. It is made after the two initial reviews and before
the adjudication decision. If the assigned adjudicator has a relevant conflict,
leave that record unresolved and rerun adjudication with a different stable,
non-personal adjudicator identifier.

The historical r2 Layer B review workload is 560 findings: the 156 FRL
and SimCSE census records plus 404 records from the other eight Layer B
repositories. All 560 remain unreviewed targets; under that frozen design, at
least 112 would have required a stratified independent second review. Its 78
Torchtune findings are separate, unreviewed diagnostic targets. A fresh
candidate reports its own Layer B and stress counts; the allocation manifest
calculates the fresh, full-population quota. Stress reports are labelled
diagnostic-only and omit effectiveness proportions. No human annotation is
complete until review records are actually entered and adjudicated.

Labels keep correctness, applicability, and utility separate and retain the
exact repository commit, Adduce version, finding identity, source locations,
reviewer confidence, root cause, verification mode, notes, and evidence links.
Each sampled record is cryptographically bound to the validated run metadata,
combined results, and repository raw JSON. Review and reporting require the
same run and reject any identity, finding-content, or artifact-digest drift.
Every v2 record also carries the same sample-set binding: the immutable-run
sampler SHA-256, sampler Python identity, exact mode, seed and selectors,
suppression policy, eligible and selected repository IDs, entry count, and
canonical fingerprint-set digest. Validation reconstructs the selection from
the immutable run and rejects legacy or mixed samples, deleted or injected
records or fields, inconsistent bindings, and sampler or runtime drift.
Independent reviewer files omit cohort assignments and other reviewers'
judgements; the interactive review command cannot display data that the file
does not contain.
Every sample record includes repository- and finding-stratum population sizes,
sample sizes, and inclusion probabilities. Effectiveness reports present
unweighted reviewed-sample proportions as descriptive summaries, never as
corpus rates. Stress reports retain diagnostic counts and agreement information
but omit those proportions. The allocation validator proves that the fresh
candidate met its exact calibration and second-review allowlists; a report must
state the resulting numerator and full Layer B denominator.

## Claim-level review

Finding labels alone do not validate Adduce’s product thesis. For each Layer B
repository, the pilot also maps one headline claim to the expected result,
run, command, configuration, data, environment, seed, and commit. The frozen
ground truth can be compared with a completed run without modifying either:

```bash
python corpus/scripts/claim_ground_truth.py evaluate \
  --claims corpus/labels/pilot-claims.json \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --run corpus/outputs/pilot-0.1.2-r6-a \
  --out corpus/reports/pilot-claim-links-r6-a.json
```

Incorrect links are more serious than missing links: no claim trail should be
called supported while it contains a demonstrably wrong association.

## Audit generated sentinel drafts

The generation-safety gate covers exactly FRL, SimCSE, and Torchtune. It
re-runs the run-bound built-in analyzer under the same static socket, process,
and write guards, requires its deterministic result projection to match the
immutable raw scan, and renders one strict NeurIPS checklist and one artifact
appendix per sentinel. The output bundle retains all six drafts, all three
complete evidence ledgers, their hashes, the source and run identities, and a
machine-checkable audit manifest.

```bash
python corpus/scripts/audit_sentinel_generation.py generate \
  --run corpus/outputs/pilot-0.1.2-r6-a \
  --clones corpus/clones/pilot-2026-07-13 \
  --out corpus/reports/pilot-generation-audit-r6-a

python corpus/scripts/audit_sentinel_generation.py validate \
  --bundle corpus/reports/pilot-generation-audit-r6-a \
  --run corpus/outputs/pilot-0.1.2-r6-a \
  --clones corpus/clones/pilot-2026-07-13
```

Exit status 0 means the exact bundle is valid and every ledger-classified
`yes` or `partial` answer passes the evidence policy. Exit status 1 retains a
structurally valid failed audit with its failure details. Exit status 2 means
the inputs or bundle are invalid, incomplete, drifted, or malformed. A `yes`
requires strict direct or author-confirmed evidence; `yes` and `partial`
cannot be evidence-free; and static text cannot imply execution without
dynamic-verified evidence. This initial pilot does not produce dynamic
evidence. If a valid real-repository bundle contains no affirmative entries,
it demonstrates conservative abstention but does not exercise the real-data
affirmative path; the report must state that limitation explicitly.

## Permitted conclusions

- Before human review is complete: synthetic controls support specified
  regression behaviour only; validated real and stress runs support operational
  completion, crash/timeout, runtime, volume, and deterministic-repeatability
  observations only.
- After the claim-review, finding-review, allocation, agreement, and
  adjudication gates pass: Layer B supports reviewed finding correctness,
  applicability, utility, claim-link behaviour, and descriptive score
  distributions, always with repository and review counts.
- Layer C always supports operational robustness only. Stress findings and
  scores never enter an effectiveness denominator.

Weights and tier thresholds remain unchanged during the pilot. Detector fixes
follow measured root causes; the score is not tuned to make a cohort look
better.

## Bounded acceptance gates

The corpus slice of the 0.1.2 trust milestone is complete only when:

1. the frozen 15-repository inventory retains the published SHA-256 and every
   row has a versioned acquisition record, including failures and partial
   submodule or Git LFS state;
2. all fourteen synthetic controls pass and the complete local gate succeeds:
   `pytest --cov=adduce --cov-report=term-missing --cov-fail-under=85`,
   `ruff check src tests scripts corpus/scripts`, and
   `mypy src/adduce scripts corpus/scripts`;
3. one candidate claim for each of the ten Layer B repositories is frozen
   before the first scan, validates against the exact checkout, and covers all
   ten link targets; two human domain reviewers independently review every
   claim and link, and the final merged or adjudicated resolution accepts the
   exact truth; the machine-valid review artifact binds the exact truth
   SHA-256, per-claim and per-link decisions and evidence, blinding
   declarations, identities, timestamps, and independent adjudication, and
   both run directories bind the same truth digest; no trail is accepted as
   `supported` when any expected link is known to be wrong;
4. two fresh built-in-only runs validate, have comparable analyzer, harness,
   environment, inventory, acquisition, and ground-truth identities, and
   produce no unexplained deterministic-output difference;
5. every repository remains represented in the results, with acquisition,
   scanner, timeout, and contract failures reported separately;
6. the three sentinel repositories receive a complete all-status,
   suppressed-inclusive census review, the remaining Layer B sample follows
   the frozen design, and the persisted allocation binds all review sources and
   exact fingerprints; the 40 calibration findings pass the agreement gate,
   the larger of 40 or 20% of all fresh Layer B targets (rounded up) receives
   independent second review with calibration included in that quota, stress is
   excluded, and disagreements are reported and adjudicated; and
7. the bounded sentinel generation command exits 0, its independent validation
   also exits 0, every ledger-classified `yes` or `partial` answer is backed by
   the recorded evidence policy, no static draft implies execution, and the
   generation-safety controls exercise backed `yes` and `partial` decisions as
   well as rejection of weak or unbacked affirmative decisions. A real sentinel
   bundle with zero affirmative entries passes the structural gate only and is
   not evidence of real-data affirmative accuracy.

Failure of a gate is a recorded pilot result, not a reason to replace a
repository or relax the contract. Detector changes are limited to the first
three general root causes supported by the review evidence. Once those changes
use the pilot labels, the pilot becomes a development set: its paired
before/after results are diagnostic only. A separately frozen confirmatory
holdout is required before publication or a generalized performance claim.
