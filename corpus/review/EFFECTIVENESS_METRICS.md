# Effectiveness metrics

Coordinator-only. This document is never placed in a reviewer packet: it
describes what completed reviews are used to compute, which is information a
blinded reviewer is not meant to hold while deciding.

Every dimension below states its numerator and its denominator. Report both,
not the ratio alone. With ten Layer B repositories and five per labelled
stratum, a single record moves most of these figures visibly, and a bare
percentage hides that.

Where this document and a tracked protocol artifact disagree, the tracked
artifact governs. The permitted conclusions at any point are set by
[`../PILOT_PROTOCOL.md`](../PILOT_PROTOCOL.md) under "Reporting limits".

## What this pilot cannot establish

- **No population false-positive rate.** The corpus is a purposive selection of
  fifteen repositories, not a sample from a defined population, and reviewed
  proportions are unweighted summaries of the reviewed records. No numerator
  and denominator on this page may be presented as a corpus rate or as a rate
  for research repositories in general.
- **No calibrated score threshold.** Nothing here supports a score cut-off, a
  tier boundary, or a badge prediction. Weights and thresholds stay fixed for
  the duration of the pilot.
- **Cohen's kappa is a descriptive diagnostic.** Where it is reported it sits
  beside the raw agreement and disagreement counts and never replaces them. It
  is undefined in degenerate single-class cases — when every decision on both
  sides takes the same value, the expected-agreement term drives the
  denominator to zero — and a kappa that is undefined, or near zero under high
  raw agreement, is a property of the marginal distribution rather than
  evidence about the reviewers. Report the counts first and the kappa second,
  or omit the kappa.
- **Static analysis is not execution.** No dimension here reports that a
  repository ran, reproduced a result, or is reproducible. Claim-link
  dimensions measure agreement between the analyzer's static resolution and an
  accepted human record.

## Tier 1 — available before candidate execution

Process instrumentation and operational observation. These do not depend on any
human review decision and may be computed and reported while the review gate is
open.

**Reviewer completion.** Numerator: decisions recorded in one reviewer's file.
Denominator: 110 (10 claims x 1 claim-level decision + 10 claims x 10
link-level decisions). Report per reviewer, never pooled into a single 220 —
two files at 55/110 each are not the same evidence as one complete file. Also
report finalized claims over 10 and declarations over 10.

**Review duration.** Numerator: sum of self-reported minutes across a
reviewer's claims. Denominator: claims with a recorded time, which may be fewer
than 10. State the denominator; a total over eight timed claims is not a total
over ten.

**Median time per claim.** The median of the per-claim minutes for one
reviewer, reported with n, the minimum and the maximum. The median is used
rather than the mean because a single interrupted claim distorts the mean at
n = 10.

**Validator failure count.** Numerator: entry attempts the tool refused, as
reported by the reviewer. Denominator: that reviewer's 10 claims. This measures
how often the apparatus obstructed the work, not reviewer competence, and it is
the dimension that should drive tool changes.

**Clarification-request count.** Numerator: clarifications a reviewer had to
request from the coordinator. Denominator: that reviewer's 10 claims. A
clarification concentrated on one claim or one target is a signal about the
guidance, and the free-text field should be read alongside the count.

**Operational failures.** Numerator: repositories in one run with an
acquisition failure, a scanner crash, a timeout, or a run-contract failure.
Denominator: 15 repositories attempted. Report the four categories separately
rather than summed; they have different causes and different remedies. A
recorded failure is a pilot result and is retained, not replaced.

**Repeatability.** Numerator: per-repository artifacts whose normalised
projection matches across the two runs of a pair. Denominator: artifacts
compared. Report alongside it the count that is byte-identical and the exact
set of JSON leaves that differ. Normalised equality is not byte identity, and
the two must never be reported as the same thing.

**Generated-artifact safety.** Numerator: ledger-classified `yes` and `partial`
answers backed by evidence meeting the recorded evidence policy. Denominator:
all `yes` and `partial` answers in the retained ledgers. Report the exit status
of both the generation command and its independent validation. A bundle with
zero affirmative entries passes the structural gate only; it demonstrates
conservative abstention and is not evidence of real-data affirmative accuracy,
and the report must say so.

## Tier 2 — available after claim review

These require both completed independent reviews bound to the same frozen
truth. Every one of them is an agreement measure between two humans. None of
them says anything about the analyzer.

**Raw agreement.** Numerator: decision positions where both reviewers recorded
the same value. Denominator: 110 comparison positions (10 claim-level + 100
link-level). 110 is the comparison denominator; 220 is the count of recorded
decisions across both files and is never a denominator for agreement.

**Claim-level agreement.** Numerator: claims where both reviewers' claim-level
decisions match. Denominator: 10.

**Link-level agreement.** Numerator: links where both reviewers' decisions
match. Denominator: 100.

**Agreement by target.** For each of the ten targets — `code`,
`reported_result`, `run`, `output`, `command`, `configuration`, `data`,
`environment`, `seed`, `commit` — numerator: claims where both reviewers agree
at that target; denominator: 10. Report all ten as counts out of ten. At this
size, treat a low count at one target as an indication of where to look next.

**Disagreement count.** 110 minus the raw-agreement numerator, listed
item by item with the claim identifier, the target, and both values. This list
is the primary artifact of this tier; the agreement ratios are a summary of it.
Never report an agreement figure without the count of compared items beside it.

**Adjudication burden.** Numerator: claims that required an independent
adjudication record. Denominator: 10. Because the schema binds one adjudication
record per claim, covering the claim-level decision and all ten links, express
the work as well: 11 decisions per adjudicated claim.

**`revision_required` count.** Numerator: decisions recorded as
`revision_required`. Denominator: 110 per reviewer. Report per reviewer and
then per position after resolution. A surviving `revision_required` is a finding
about the frozen record and is reported as one.

**`unclear` count.** Numerator: decisions recorded as `unclear`. Denominator:
110 per reviewer, with the same per-position breakdown after resolution.
`unclear` is neither a failure nor a pass; a cluster of `unclear` at one target
is evidence about what the pinned evidence can settle, and it belongs in the
report with its notes.

## Tier 3 — available after finding review

The claim-link dimensions are computable as soon as the candidate pair has
executed, but they are reportable only after the claim-review, finding-review,
allocation, agreement and adjudication gates have all passed. That ordering is
set by the protocol's reporting limits: until the finding review is complete,
claim-link behaviour is not among the permitted conclusions.

All four dimensions below use the *accepted* record as their reference. A link
whose expectation the completed review did not resolve as `verified` is not
ground truth; exclude it, and report both the exclusion count and the reduced
denominator.

**Candidate confusion matrix.** Rows: the resolution the accepted record
expects. Columns: the resolution the candidate run reports. Cell: link count.
Denominator: accepted links, at most 100 per run. Report the matrix itself, not
a single accuracy figure derived from it.

**Per-target candidate performance.** For each of the ten targets, numerator:
accepted links at that target where the candidate's resolution equals the
expected resolution; denominator: accepted links at that target, at most 10.
Present as counts out of ten. A percentage over a denominator of ten is
misleading on its own.

**Macro-F1 over the three resolution classes.** For each class in `resolved`,
`unresolved`, `not_applicable`: precision = TP / (TP + FP), recall =
TP / (TP + FN), F1 = 2 x precision x recall / (precision + recall), where TP,
FP and FN are link counts over the accepted links. Macro-F1 is the unweighted
mean of the per-class F1 values; the denominator of that mean is the number of
classes averaged. A class with zero support in the accepted record contributes
no F1 and is excluded from the mean — state which classes were averaged.
Report every per-class count alongside the score, because at this size one link
moves the macro average by a visible margin.

**Abstention quality.** Two figures, reported together and never merged.
Declining-when-expected — numerator: accepted links whose expected resolution
is `unresolved` or `not_applicable` and where the candidate also declined to
resolve; denominator: accepted links with a declining expectation.
Over-declining — numerator: accepted links whose expected resolution is
`resolved` but where the candidate declined; denominator: accepted links
expecting `resolved`. Neither figure is a correctness rate for the corpus, and
declining to resolve is the intended behaviour when evidence is absent.

## Tier 4 — available only in a later confirmatory holdout

Nothing measured on this pilot moves into this tier by being recomputed. Tier 4
is the tier-3 dimensions evaluated on a separate confirmatory holdout, frozen
with its inventory, hypotheses, sampling plan and acceptance rules fixed before
any of its results are inspected.

The following belong here and nowhere earlier: any generalized claim-link
accuracy figure; any population false-positive rate; any calibrated score
threshold, tier boundary, or badge prediction; and any cross-machine
performance comparison. Once findings from this pilot inform a detector change,
the pilot is a development set, and same-commit before/after runs over it are
paired diagnostics rather than unbiased accuracy estimates.

## Reporting rules that apply to every tier

- State the numerator, the denominator, and the number of repositories behind
  every figure.
- Keep stress-cohort records out of every effectiveness denominator. They
  support operational observation only.
- Report a failed gate as a result. Do not replace a repository, relax a
  contract, or re-draw a sample to move a number.
- Do not report a figure whose gate has not passed, even when the inputs to
  compute it are already on disk.
