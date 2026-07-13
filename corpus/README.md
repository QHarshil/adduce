# Validation corpus

Adduce uses three separate evidence layers because detector regression,
reviewed decision correctness and claim-link behaviour, and operational
robustness are different questions.
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
frozen ground-truth SHA-256. Passing the same file to the runner copies it into
the immutable run evidence and records that digest.

## Run twice and validate

Use fresh output directories. Each repository gets 300 seconds in the pilot.
The scanner runs only canonical built-in rules, installs a Python audit guard
against socket and non-metadata subprocess activity, and hashes repository
bytes before and after each check. The audit guard detects scanner regressions;
it is not an operating-system sandbox.

```bash
python corpus/scripts/run_validation.py \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --claims corpus/labels/pilot-claims.json \
  --out corpus/outputs/pilot-0.1.2dev0-r2-a \
  --timeout 300

python corpus/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2dev0-r2-a

python corpus/scripts/run_validation.py \
  --repos corpus/repos.csv \
  --clones corpus/clones/pilot-2026-07-13 \
  --claims corpus/labels/pilot-claims.json \
  --out corpus/outputs/pilot-0.1.2dev0-r2-b \
  --timeout 300

python corpus/scripts/validate_run.py \
  corpus/outputs/pilot-0.1.2dev0-r2-b

python corpus/scripts/compare_runs.py \
  corpus/outputs/pilot-0.1.2dev0-r2-a \
  corpus/outputs/pilot-0.1.2dev0-r2-b \
  --out corpus/reports/pilot-determinism-r2.json
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

The initial directory `corpus/outputs/pilot-0.1.2dev0-a` is a retained
preflight failure: all 15 scanner payloads were rejected because a relative
clone argument was resolved again after the child working directory changed.
It contains no accepted raw results and is excluded from effectiveness and
repeatability analysis. The absolute-path correction produced a valid,
deterministic `r1-a`/`r1-b` pair. A subsequent generation-audit preflight
stopped on a repository `SyntaxWarning`; the narrowly amended, final harness
uses the fresh `r2-a` and `r2-b` directories shown above. Both failed
preflights and the valid `r1` pair remain retained. See the protocol
amendments for the fixed scope and rationale.

## Produce a descriptive report

Reports are written outside the immutable run directory.

```bash
python corpus/scripts/summarize.py \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --out corpus/reports/pilot-summary-r2.md
```

The report keeps evaluated, unvetted, and stress roles distinct. A frequent
fail/partial rule is described as chatty until manual review establishes
whether it is noisy.

## Draw and review a finding sample

Use the operational definitions and edge-case rules in the
[`ANNOTATION_GUIDE.md`](ANNOTATION_GUIDE.md) for every review. First draw a
complete census of all findings for the predeclared FRL, SimCSE, and Torchtune
sentinels:

```bash
python corpus/scripts/sample_findings.py \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --census \
  --include-repo frl \
  --include-repo simcse \
  --include-repo torchtune \
  --statuses pass,partial,fail,unknown,not-applicable \
  --seed 0 \
  --out corpus/labels/pilot-sentinels-r2.jsonl
```

The census includes suppressed findings by default. Do not use
`--exclude-suppressed` for the primary pilot; it exists only for a separately
declared sensitivity analysis.

Then sample all statuses from the remaining Layer B repositories so false
passes, applicability errors, and inappropriate abstentions can be detected.
Finding sampling excludes the stress cohort by default. The command below also
excludes the two Layer B sentinels already included in the census. The seed
makes repository and status/category-stratum selection repeatable.

Run the independent review, adjudication, and report sequence below for the
sentinel census and then for the remaining Layer B sample; the commands show
the latter filename.

```bash
python corpus/scripts/sample_findings.py \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --n-repos 8 \
  --exclude-repo frl \
  --exclude-repo simcse \
  --per-stratum 2 \
  --statuses pass,partial,fail,unknown,not-applicable \
  --seed 0 \
  --out corpus/labels/pilot-sample-r2.jsonl

python corpus/scripts/label_findings.py \
  corpus/labels/pilot-sample-r2.jsonl \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --reviewer-id reviewer-1

python corpus/scripts/label_findings.py \
  corpus/labels/pilot-sample-r2.jsonl \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --reviewer-id reviewer-2

python corpus/scripts/label_findings.py \
  corpus/labels/pilot-sample-r2.jsonl \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --adjudicate \
  --reviewer-id adjudicator-1

python corpus/scripts/label_findings.py \
  corpus/labels/pilot-sample-r2.jsonl \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --report
```

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
The initial review interface hides cohort and other reviewers' judgements.
Every sample record includes repository- and finding-stratum population sizes,
sample sizes, and inclusion probabilities. The report presents unweighted
reviewed-sample proportions as descriptive summaries, never as corpus rates.
At least 20% of the first 100–200 findings require independent second review;
the command above uses the stronger design of second-reviewing the full sample.

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
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --out corpus/reports/pilot-claim-links-r2-a.json
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
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
  --clones corpus/clones/pilot-2026-07-13 \
  --out corpus/reports/pilot-generation-audit-r2-a

python corpus/scripts/audit_sentinel_generation.py validate \
  --bundle corpus/reports/pilot-generation-audit-r2-a \
  --run corpus/outputs/pilot-0.1.2dev0-r2-a \
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

- Synthetic controls: specified regression behaviour only.
- Labelled real repositories: reviewed finding correctness, applicability,
  utility, claim-link behaviour, and descriptive score distributions, always
  with repository and review counts.
- Stress repositories: operational robustness only.

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
   `ruff check src tests corpus/scripts`, and
   `mypy src/adduce corpus/scripts`;
3. one candidate claim for each of the ten Layer B repositories is frozen
   before the first scan, independently reviewed by two human domain reviewers,
   validates against the exact checkout, covers all ten link targets, and is
   bound by SHA-256 into both run directories; the review record binds that
   digest, and no trail is accepted as `supported` when any expected link is
   known to be wrong;
4. two fresh built-in-only runs validate, have comparable analyzer, harness,
   environment, inventory, acquisition, and ground-truth identities, and
   produce no unexplained deterministic-output difference;
5. every repository remains represented in the results, with acquisition,
   scanner, timeout, and contract failures reported separately;
6. the three sentinel repositories receive a complete all-status,
   suppressed-inclusive census review, the remaining Layer B sample follows
   the frozen design, at least 20% receives independent second review, and
   disagreements are reported and adjudicated; and
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
