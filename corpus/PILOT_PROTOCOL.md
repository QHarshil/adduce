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
- Repository metadata uses only an allowlisted read-only Git operation after
  `--no-pager -c core.fsmonitor=false -c core.quotePath=true -C <root>`.
  Git-specific host environment variables and system/global configuration are
  removed; the audit hook permits the subprocess stdin handle to open only the
  platform null device and still rejects repository or host-file writes.
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
- Effectiveness runs require that analyzer tree to be clean and attributable to
  a full Git commit before any output directory is created. Operational-only
  development runs may be dirty or outside Git, but cannot support detector,
  score, or claim-link effectiveness conclusions.
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
Because this changed the immutable harness, the resulting historical
comparable pair is named `pilot-0.1.2dev0-r2-a` and
`pilot-0.1.2dev0-r2-b`; its retained reports, samples, and generation audit
target `r2-a`. The repository inventory,
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

### Protocol amendment 4: post-change candidate-pair preregistration

Amended: 2026-07-20 (pre-registered before execution)

The r2 pair is immutable historical evidence produced by its frozen harness.
Current scripts must not validate, compare, sample, label, evaluate, summarize,
or regenerate r2. If historical verification is needed, use the copies under
each r2 run's `harness/` directory and do not modify the retained artifacts.
These read-only commands use only the frozen harness, omit output paths, and
disable bytecode writes:

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

The current trust-milestone candidate pair is pre-registered as
`pilot-0.1.2dev0-r3-a` and `pilot-0.1.2dev0-r3-b`. Neither candidate directory
has been executed or created as part of this amendment.

The tracked `corpus/pilot-r3-preregistration.json` is the machine-readable
prospective lock. It records the exact pair names, 300-second timeout, analyzer,
rule-set and dependency identity, repository, clone, truth and provenance
digests, reviewer/offline/no-plugin execution policy, and the complete
analysis-plan file map. Effectiveness preflight refuses before creating an
output directory unless the analyzer, preregistration, and every required
harness file are tracked and clean at one Git `HEAD`. The locked protocol does
not embed the preregistration file’s own digest because that would be
self-referential; each run instead records the exact input bytes and SHA-256.

Both candidates retain the frozen inventory, clone manifest, 300-second
timeout, offline built-in-only execution, default configuration, and one
headline claim per Layer B repository. The pre-registered candidate truth is
`corpus/labels/pilot-claims.json` with SHA-256
`9a26d06c59070173ad89f60bc221a395dd1a487132eeed7415d2cadeff63611e`.
Before `r3-a` starts, two independent human domain reviewers must review every
claim and every expected link in separate machine-valid
`claim-review.schema.json` files. The final merged or adjudicated resolution
must accept the exact frozen truth. The reviewers must declare that they did
not see one another's decisions or any Adduce claim-link output for the bound
truth, including retained r2 evaluations; any decision disagreement requires
an independent adjudicator. Before any decision, each initial reviewer and
adjudicator must make a structured, time-stamped declaration scoped to the
assigned repository and claim. It must affirm no relevant authorship or
contribution; close collaboration, supervision, or employment; financial
conflict; or personal conflict. A person who cannot make every affirmation is
recused and the assignment is transferred; disclosure alone is insufficient.
A neutral coordinator must issue two identical empty scaffolds and keep the
completed files mutually inaccessible until both are returned.
`claim_review.py merge` then combines them deterministically and binds both
source SHA-256 values and reviewer identities without adding an adjudication.
If review changes the truth file or its digest, this r3
preregistration is void: freeze a new truth version, record a new dated
amendment, and choose new candidate-pair names before any scan.

The effectiveness runner requires the accepted merged review and both
independent source files as explicit inputs. Before creating either candidate
directory, it reconstructs the merge and verifies the exact truth digest, the
candidate label, complete accepted decisions, source hashes, and review and
adjudication timestamps that precede the run start. It also rejects a dirty or
unknown analyzer Git state. The runner copies the truth, merged review, and both
review-source byte streams into the immutable input bundle and records their
SHA-256 digests. An operational-only run omits these inputs and cannot satisfy
this effectiveness protocol.

After the pair validates and compares without an unexplained deterministic
difference, finding review uses the predeclared sentinel census and remaining
Layer B sample with seed 0. Before annotation, `review_allocation.py` freezes a
40-finding handbook-calibration allowlist across both pristine sample sources
and an independent-second-review allowlist of exactly the larger of 40 or 20%
of all fresh Layer B targets, rounded up. Selection is SHA-256-ranked and
stratified by repository and emitted, pass, and abstention state. The 40
calibration findings are a subset of, not an addition to, the second-review
quota. Stress records are excluded from both allowlists. Any change to these
inputs, parameters, candidate names, truth digest, or acceptance rules requires
a new dated amendment before execution.

### Protocol amendment 5: post-harness-change re-lock and candidate rename

Amended: 2026-07-29 (pre-registered before execution)

The `r3` preregistration is void. It locked the analysis-plan bytes as they stood
on 2026-07-20; eight of its twenty-three analysis-plan files have changed since:
`ANNOTATION_GUIDE.md`, `PILOT_PROTOCOL.md`, `README.md`,
`claim-review.schema.json`, `finding-review.schema.json`,
`scripts/audit_sentinel_generation.py`, `scripts/claim_review.py`, and
`scripts/label_findings.py`. The `claim-review.schema.json` digest bound in the
locked inputs changed with them. A lock that does not describe the harness it
governs cannot constrain anything, so it is retired rather than reinterpreted.
This amendment changes two further analysis-plan files — `scripts/run_validation.py`,
whose `PREREGISTRATION_PATH` now targets the `r4` lock and whose `--timeout`
default is aligned to the protocol's 300 seconds, closing a window in which an
operational run that omitted the flag would have recorded 120; and
`scripts/preregistration.py`, a stale diagnostic string — and it further
modifies `PILOT_PROTOCOL.md` and `README.md`, which were already among the
eight. The `r4` lock therefore records ten changed analysis-plan digests, and
is generated from the final bytes of every file it binds, this amendment
included.

Neither `pilot-0.1.2dev0-r3-a` nor `pilot-0.1.2dev0-r3-b` was created or
executed. No run output, claim label, sample, review, adjudication, or score is
affected, and no artifact is discarded. Following amendments 1 and 2, where a
changed harness produced a newly named comparable pair rather than a reused one,
the current trust-milestone candidate pair is pre-registered as
`pilot-0.1.2dev0-r4-a` and `pilot-0.1.2dev0-r4-b`, with the machine-readable
prospective lock at `corpus/pilot-r4-preregistration.json`. Neither `r4`
candidate directory has been created or executed as part of this amendment.
Retaining the `r3` name for different bytes would leave two distinct documents
each claiming to be the prospective lock, which is the precise ambiguity
preregistration exists to remove.

This amendment changes no input, parameter, or acceptance rule. Unchanged: the
frozen repository inventory and its SHA-256, the clone manifest and clone
snapshot set, the badged provenance record, the 300-second per-repository
timeout, offline built-in-only execution with plugins disabled, default
configuration, reviewer mode, the minimal credential-free child environment, the
sampling design and seed 0, the cohort composition, the sentinel census for FRL,
SimCSE and Torchtune, the handbook-calibration and second-review quotas, the
two-independent-reviewer claim-review requirement with its blinding and
conflict-of-interest conditions and neutral coordination, and every acceptance
rule. The pre-registered candidate truth remains `corpus/labels/pilot-claims.json`
with SHA-256
`9a26d06c59070173ad89f60bc221a395dd1a487132eeed7415d2cadeff63611e`.
Because that truth file and its digest are unchanged, the void condition
recorded in amendment 4 for a changed truth was never triggered. The analyzer
source byte tree also changed, from
`2bb6b9175f0ba475cbe9be1fdd19ab5b16aef0583cda24ddd57febb70d9e783f` to the
value recorded in `corpus/pilot-r4-preregistration.json`; the built-in rule
count of 78, the built-in rule-ID inventory digest, and the dependency-version
digest are all unchanged, so no detector behaviour is implicated. This document
distinguishes the analyzer from the corpus harness, and both moved.

Two operational-only runs, `pilot-0.1.2dev0-ops-a` and `-ops-b`, were executed
against the frozen clone set on 2026-07-29 while the analyzer tree was dirty.
They are excluded from every effectiveness, score-separation, claim-link, and
false-positive denominator, and they bind no truth, review, preregistration
digest, or candidate-run name. Their analyzer source tree differs from the `r1`
and `r2` pairs, so they are not comparable to them and no cross-pair comparison
was attempted. They are retained as operational evidence only.

The claim-review and finding-review gates remain open, and the reporting limits
below continue to apply in full. This amendment was prepared from file digests
and the retained protocol text alone. No `r3` or `r4` claim-link output exists,
and no retained `r2` evaluation informed it.

### Protocol amendment 6: post-version-bump re-lock and candidate rename

Amended: 2026-07-29 (pre-registered before execution)

The `r4` preregistration is void. The release of 0.1.2 changed the analyzer:
`src/adduce/__init__.py` carries `__version__`, and that file lives inside the
byte tree that `_source_tree_sha256` hashes, so bumping the stable version
changed both the recorded `adduce.version` (`0.1.2.dev0` → `0.1.2`) and the
analyzer source-tree digest. The digest moved from
`c0f588b73be710b970a179679900d6334f4c1c56fcc048d747b9c0bdf358ff99`, the value
recorded in `corpus/pilot-r4-preregistration.json`, to the value recorded in
the new `corpus/pilot-r5-preregistration.json`.

Amendment 5 left open whether a never-executed candidate pair may have its
lock regenerated in place, or whether any analyzer or harness byte change
forces a new dated amendment and a new pair name. Owner decision, 2026-07-29:
the strict reading governs — a new dated amendment and a new candidate-pair
name, always. In-place regeneration of a lock is not permitted. This is a
standing rule, not merely this amendment's choice, and the question does not
reopen. The reasoning, following amendment 5: retaining a name for different
bytes would leave two distinct documents each claiming to be the prospective
lock, which is the precise ambiguity preregistration exists to remove.

Neither `pilot-0.1.2dev0-r4-a` nor `pilot-0.1.2dev0-r4-b` was ever created or
executed, so no run output, claim label, sample, review, adjudication, or
score is affected, and no artifact is discarded. Following the standing rule
above and amendments 1, 2, and 5, the current trust-milestone candidate pair
is pre-registered as `pilot-0.1.2-r5-a` and `pilot-0.1.2-r5-b`, with the
machine-readable prospective lock at `corpus/pilot-r5-preregistration.json`
and protocol ID `pilot-0.1.2-r5`. The name drops the `dev0` suffix because the
analyzer under test is now the stable 0.1.2, not a development build. Neither
`r5` candidate directory has been created or executed as part of this
amendment.

This amendment changes `PILOT_PROTOCOL.md` itself and `README.md`, and it
changes `scripts/run_validation.py`, whose `PREREGISTRATION_PATH` now targets
the `r5` lock. The `r5` lock is generated from the final bytes of every file
it binds, this amendment included.

This amendment changes no input, parameter, or acceptance rule beyond the
re-lock and rename above. Unchanged: the frozen repository inventory and its
SHA-256, the clone manifest and clone snapshot set, the badged provenance
record, the 300-second per-repository timeout, offline built-in-only
execution with plugins disabled, default configuration, reviewer mode, the
minimal credential-free child environment, the sampling design and seed 0,
the cohort composition (5 badged_functional, 5 stress, 5 unvetted, 15
repositories), the sentinel census for FRL, SimCSE and Torchtune, the
handbook-calibration and second-review quotas, the two-independent-reviewer
claim-review requirement with its blinding and conflict-of-interest
conditions and neutral coordination, and every acceptance rule. The
pre-registered candidate truth remains `corpus/labels/pilot-claims.json` with
SHA-256 `9a26d06c59070173ad89f60bc221a395dd1a487132eeed7415d2cadeff63611e`.
Because that truth file and its digest are unchanged, the void condition
recorded in amendment 4 for a changed truth was never triggered. The built-in
rule count of 78, the built-in rule-ID inventory digest, and the
dependency-version digest are all unchanged: the change is a version string
and nothing else, so no detector behaviour is implicated.

The claim-review and finding-review gates remain open. The
two-independent-human-reviewer claim review has not been performed; the `r4`
review scaffolds were issued but returned empty, so no human decision exists
for any candidate pair. New scaffolds bound to the `r5` pair will be issued
before `r5-a` starts. No `r3`, `r4`, or `r5` claim-link output exists. This
amendment was prepared from file digests and the retained protocol text
alone; no retained `r2` evaluation informed it.

### Protocol amendment 7: post-platform-fix re-lock and candidate rename

Amended: 2026-07-31 (pre-registered before execution)

The `r5` preregistration is void. Correcting a set of Windows-specific defects
changed both the analyzer and the harness. Descriptors in the write boundary and
in the execution layer were opened in the C runtime's default text mode, so on
Windows a written newline became a carriage-return/newline pair, the on-disk
size then exceeded the payload length, and adduce could not write its own
artifacts; and the corpus harness resolved Git after narrowing its child
environment, and acquired clones under a different Git configuration than it
later audited them with. The fixes moved `src/adduce/safe_write.py` and
`src/adduce/dynamic/reproduce.py`, which lie inside the byte tree that
`_source_tree_sha256` hashes, and `scripts/check_builtin.py`,
`scripts/clone_repos.py` and `scripts/run_validation.py`, which lie inside the
analysis plan. The recorded `adduce.version` is unchanged at `0.1.2`: this is a
correctness fix on a prepared release, not a new release.

Amendment 6 stated that the `r4` review scaffolds "were issued but returned
empty". That clause is withdrawn. This protocol defines a returned review as a
reviewer's handoff of a completed file to the coordinator, and no reviewer ever
received either `r4` file, so no return occurred and none can be reported. The
conclusion amendment 6 drew from that clause stands, and is restated here on its
own evidence: no human claim-review decision exists for any candidate pair.
Amendment 6's recorded text is left exactly as written. A preregistration is a
record of what was believed and when it was believed, so an error in one is
superseded by a later dated amendment, never edited in place.

Neither `pilot-0.1.2-r5-a` nor `pilot-0.1.2-r5-b` was ever created or executed,
so no run output, claim label, sample, review, adjudication, or score is
affected, and no artifact is discarded. Following the standing rule recorded in
amendment 6, the current trust-milestone candidate pair is pre-registered as
`pilot-0.1.2-r6-a` and `pilot-0.1.2-r6-b`, with the machine-readable prospective
lock at `corpus/pilot-r6-preregistration.json` and protocol ID `pilot-0.1.2-r6`.
Neither `r6` candidate directory has been created or executed as part of this
amendment.

This amendment changes `PILOT_PROTOCOL.md` itself and `README.md`, and it
changes `scripts/run_validation.py`, whose `PREREGISTRATION_PATH` now targets
the `r6` lock, and `scripts/run_contract.py`. The `r6` lock is generated from
the final bytes of every file it binds, this amendment included.

The analyzer and harness were exercised on the supported platform matrix before
this amendment was prepared. Continuous-integration run `30616431576` covers the
Windows platform fixes named above and reports exactly one failing test on every
leg, including Windows: the lock-identity check this amendment resolves. The
retired acceptance check recorded below landed after that run, so it will be
covered by continuous integration on the commit that registers this amendment,
and that leg is to be read before any `r6` candidate is executed. Locking ahead
of either would have pre-registered a harness that no supported platform had
exercised.

One acceptance check is retired here, and the reason is recorded rather than
left to inference. `run_validation.py` recomputed the expected clone-tool digest
from the live `scripts/clone_repos.py` and refused any run whose clone manifest
declared a different one. The frozen clone set was acquired on 2026-07-13, and
the Windows fix named above changed that tool afterwards, so from that point
`run_validation.py` refused every new run over the frozen corpus before creating
an output directory. `run_contract.py` carried the same comparison at run
finalisation, against the harness digest each run recorded for itself; it never
refused anything, because the earlier check always stopped the run first, and it
continues to accept the retained runs, whose recorded digest matched their
manifest at the time. Lifting only the first check made the second one refuse in
its place, at the end of an otherwise complete run. A later patch to an
acquisition tool cannot alter bytes already acquired, so the refusal was
incorrect rather than protective, and both comparisons against a
later-than-acquisition digest are removed. What establishes
clone integrity is unchanged and is re-verified on every load: the per-clone
`git_tree_sha`, `worktree_sha256` and `repository_tree_sha256`, with the origin
and `HEAD` re-checks beside them. What establishes manifest authenticity for an
effectiveness run is this lock, because validation rebuilds the entire expected
payload from live inputs, so `inputs.clone_manifest_sha256` binds the manifest
bytes and the acquisition-tool digest recorded inside them. A manifest must
still declare a well-formed digest or the run is refused; every run now records
that digest and whether it matches the current tool; and run finalisation
compares the copied manifest against the value the run itself recorded, so a
manifest substituted after a run is still refused. No acceptance rule bearing on
any pilot conclusion changes.

This amendment changes no input, parameter, or acceptance rule beyond the
re-lock, the rename, and the retired check recorded above. Unchanged: the frozen
repository inventory and its SHA-256, the clone manifest including the
acquisition-tool digest it records, the clone snapshot set, the badged provenance
record, the 300-second per-repository timeout, offline built-in-only execution
with plugins disabled, default configuration, reviewer mode, the minimal
credential-free child environment, the sampling design and seed 0, the cohort
composition (5 badged_functional, 5 stress, 5 unvetted, 15 repositories), the
sentinel census for FRL, SimCSE and Torchtune, the handbook-calibration and
second-review quotas, the two-independent-reviewer claim-review requirement with
its blinding and conflict-of-interest conditions and neutral coordination, and
every acceptance rule. The pre-registered candidate truth remains
`corpus/labels/pilot-claims.json` with SHA-256
`9a26d06c59070173ad89f60bc221a395dd1a487132eeed7415d2cadeff63611e`. Because that
truth file and its digest are unchanged, the void condition recorded in
amendment 4 for a changed truth was never triggered. The built-in rule count of
78 and the built-in rule-ID inventory digest are unchanged, and no rule function
was edited.

Unlike amendment 6, observable behaviour is implicated, on one platform, and the
finding is stronger than a detector defect. Before these fixes adduce could not
write its own artifacts on Windows at all: the write boundary opened descriptors
in text mode, a written newline became a carriage-return/newline pair, the
resulting file exceeded the payload length, the boundary correctly refused, and
the partially written file was removed. Every command that writes therefore
exited 2 on Windows, so no artifact and no evidence-ledger record was produced
and no generation observation was obtainable there. The write boundary was
working as designed; what was wrong was the descriptor mode beneath it. No
built-in rule is implicated, the rule inventory is unchanged, and no rule
function was edited. No candidate pair has ever been executed on Windows, so no
recorded pilot observation is affected, and any Windows observation predating
this amendment would have to be discarded rather than interpreted.

The claim-review and finding-review gates remain open. The
two-independent-human-reviewer claim review has not been performed, and no human
decision exists for any candidate pair. The two empty scaffolds bound to the
`r5` pair are void with the `r5` lock; no reviewer has held either, so nothing
is lost. New scaffolds bound to the `r6` pair will be issued before `r6-a`
starts, and the `r4` and `r5` scaffolds are not distributed. No `r3`,
`r4`, `r5`, or `r6` claim-link output exists. Effectiveness gate 1, a clean
committed analyzer with the preregistration tracked at one Git `HEAD`, is not
satisfied until the `r6` lock is generated, tracked, and clean. This amendment
was prepared from file digests, the recorded Git history of the files it names,
and the retained protocol text alone; no retained `r2` evaluation informed it.

### Protocol amendment 8: retirement of r6 and an unlocked development interval

Amended: 2026-08-28 (pre-registered before execution)

The `r6` preregistration is void and is not replaced in kind. Amendments 5, 6
and 7 each answered a byte change by re-locking: a new dated amendment, a new
candidate-pair name, a new machine-readable lock. That is the correct answer
when the analyzer is settled and the change is an exception. It is the wrong
answer now, because the analyzer is being rebuilt on purpose and the exceptions
have become the rule.

The cost is visible in this protocol's own records. The void record began by
naming two changed analysis-plan files, grew to three, then five, and now names
six. Every
correction to a hashed file — including two corpus documents whose text was
simply wrong — has had to be enumerated against a lock that governs nothing.
Enumerating every edit against a dead lock recreates preregistration while
claiming none exists: the bookkeeping of a lock without the guarantee.

This amendment opens an **unlocked development interval**, and distinguishes
what the interval frees from what it does not. The distinction is not locked
versus unlocked. It is **experimental data versus analysis machinery**.

#### Frozen for the duration of the interval

These are the study's material. Changing them after development results have
been seen changes the experimental question retroactively, which is the precise
harm preregistration exists to prevent. A change to any of them requires a
further dated amendment **before** it is made.

- the repository inventory and its exact pinned revisions (`corpus/repos.csv`,
  the clone manifest, and the clone snapshot set)
- the badged-provenance identities
- the frozen claim ground truth `9a26d06c…`
- any completed human decision, and the bytes its validity depends on

The last clause is conditional and currently vacuous: no reviewer decision
exists for any candidate pair, and the r4, r5 and r6 review files are empty
scaffolds. It becomes operative the moment a decision is collected, and at that
point the claim-review schema and anything else that decision was made against
join the frozen set automatically, without a further amendment.

#### Free for the duration of the interval

These are how the study is conducted, not what it is about. They may change
without record, without enumeration, and without amendment.

- the analyzer source tree and rule implementations
- the built-in rule-ID inventory, including adding, removing or splitting rules
- the preregistration and report schemas
- the run harness, the analysis scripts, and the analysis-plan documentation
- the 300-second per-repository timeout, which amendment 7 carried as a frozen
  parameter and which becomes free here because no figure it could shift is
  reportable while the interval is open; `r7` locks it again

Freezing these would buy little against researcher degrees of freedom while
creating protocol bureaucracy. If the effectiveness work shows a rule must
split, a weak rule must go, or a schema needs one more field, that is the
interval working as intended.

#### Frozen truth may be checked, not tuned against

For the duration of the interval, the frozen claim ground truth may be used to
verify integrity — that its digest still matches, that the corpus still loads,
that a run still completes — and for nothing else.

It **must not** be used to tune analyzer behaviour. Running the fifteen
repositories for crashes, determinism, output-contract conformance and
detector-regression comparison is permitted and expected. Repeatedly consulting
the human answers and adjusting until those specific answers improve is not. A
frozen test set whose bytes never move is still converted into a development set
by being optimised against, and the resulting effectiveness figure would be
measured on data the analyzer had been fitted to.

#### Reporting restrictions, unchanged

No effectiveness, calibration, false-positive, score-separation,
badge-prediction or calibrated-tier figure may be stated, published, or carried
into a release note while the interval is open. No candidate-pair name is
reserved or consumed. `r7` is not pre-registered by this amendment.

#### What replaces the lock

Not prose. Executable assertions:

1. the repository inventory digest is unchanged (`corpus/repos.csv`)
2. the badged-provenance digest is unchanged
3. the frozen claim ground truth digest is `9a26d06c…`
4. effectiveness execution fails closed while no live lock exists

Assertions 1, 2 and 4 run in any checkout and fail the build. The clone
manifest, the clone snapshot set and the claim ground truth are gitignored study
data that a fresh checkout does not contain: they are verified wherever the
local corpus is present, and their frozen digests are pinned additionally
against the retired `r6` record, which is tracked.

The `_R6_VOID` record, and the two functions that maintain it, are deleted. The
assertions that pinned the analyzer digest, the analysis-plan file set, the
preregistration schema, the built-in rule-ID digest, the claim-review schema,
the candidate-pair names, the protocol ID, the analyzer version, the timeout and
the cohort counts are deleted with it.

Three of those deletions are enforcement, not bookkeeping, and must return when
`r7` is registered: the pins on `protocol_id`, `adduce.version` and
`candidate_pair` are what would catch a lock regenerated in place, which
amendment 6 forbids as a standing rule. They are safe to delete only while no
lock exists — there is nothing to regenerate. The amendment registering `r7`
restores them against the new lock, and is not complete without them.

#### Expiry

The interval ends when the 0.3 analyzer and the analysis plan are frozen. A
further dated amendment then registers `r7` against the finished analyzer, under
the standing rule of amendment 6: a new dated amendment and a new candidate-pair
name, always; in-place regeneration of a lock is not permitted.

The fifteen pinned repositories are retained beyond `r7` as a longitudinal pilot
benchmark. A headline effectiveness claim at 0.3 or later is expected to rest
additionally on a fresh independent holdout prepared after the analyzer is
frozen, so that external validity does not depend on a corpus the project has
had continuous access to.

## Ground truth and review

Before using the pilot for detector changes:

1. After acquisition and before the first scan, map one headline claim in each
   Layer B repository to the expected code, reported result, run, output,
   command, configuration, data, environment, seed, and commit. Use `unknown`
   or `not_applicable` rather than inventing a link. Validate the local records
   with `claim_ground_truth.py` and retain their SHA-256. No claim ground-truth
   records are pre-populated by the project. If a labelled snapshot cannot be
   acquired, bind an explicit unavailable record to the failed clone-manifest
   entry; do not invent a claim source or remove the repository. Two distinct
   human domain reviewers independently review every frozen claim and each of
   its ten expected links in separate copies of one empty scaffold. A neutral
   coordinator withholds the other copy until both are complete, then performs
   the deterministic merge. The final merged or adjudicated resolution must
   accept the exact truth. The machine-valid merged artifact binds the truth
   SHA-256, both reviewer-source SHA-256 values, evidence, identities,
   timestamps, blinding and conflict-of-interest declarations, decisions, and
   any later independent adjudication. Declarations contain stable,
   non-personal identifiers and assignment scope only; they do not contain
   names, employers, relationship details, or recusal reasons. Future and
   confirmatory truth sets complete and accept that
   review before scanning; the retained r2 candidate follows the historical
   exception recorded in protocol clarification 3. Automated checks may
   validate structure and source identity, but they are not reviewer decisions.
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

Before annotation, the allocation artifact binds both pristine sample-source
SHA-256 values, their immutable identity projections and sample-set hashes, the
candidate run, the selector source, the seed, every selected fingerprint, and
repository/status strata. It initializes a primary reviewer file covering the
complete Layer B population and a secondary reviewer file covering exactly the
second-review allocation. Forty allocated Layer B findings receive two reviews
for handbook calibration. Every Layer B target receives a primary review. At
least 20% of all fresh Layer B review targets, rounded up and stratified
reproducibly, receive independent second review; the 40 calibration findings
count toward that quota. Independent files omit cohort assignments and all
other reviewer decisions. The deterministic merge binds both completed
reviewer-file SHA-256 values, roles, identities, expertise statements, and
blinding and conflict-of-interest declarations without changing either initial
decision. Each reviewer declaration is bound to the exact repository set and
finding-fingerprint-set digest and precedes the first decision. Disagreements
receive an explicit adjudication record with its own assignment-scoped
declaration, and agreement counts accompany every descriptive aggregate.
Anyone with relevant authorship or contribution; close collaboration,
supervision, or employment; financial conflict; or personal conflict is
recused and replaced rather than retained with a disclosure. Stress findings
never enter either effectiveness assignment. Unweighted reviewed-sample
proportions are not corpus rates.

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

Before human review is complete, the pilot may report only synthetic-control
behaviour and operational observations from validated runs. After the claim
review, finding review, second-review quota, and adjudication gates are complete,
the pilot may report reviewed counts, repository-level distributions,
machine-local runtime and resource observations, crash/timeout outcomes,
reviewer agreement, and carefully scoped rule-level observations. With five
repositories per labelled stratum, all comparisons remain exploratory. No
score threshold, badge prediction, calibrated tier, cross-machine performance
comparison, or population false-positive claim follows from this pilot alone.
