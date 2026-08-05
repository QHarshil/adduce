# Finding review — reviewer guide

This gate is not used yet. It opens only after the claim-ground-truth gate is
complete and the candidate-execution prerequisites are satisfied: a validated
pair of runs over the frozen corpus, a drawn and validated sample, and a frozen
allocation manifest. Until the coordinator hands you a role-bound review file
built from those inputs, there is nothing here to do.

The definitions are in [`../ANNOTATION_GUIDE.md`](../ANNOTATION_GUIDE.md). This
guide does not repeat them. It tells you how to work through an assignment and
points at the section that settles each question.

## What you are deciding

One sampled analyzer finding, at one pinned repository commit. You judge the
complete recorded decision — status, scope, message, locations, and, where
present, suppression — not only its headline. Read the documented rule contract
first, then the pinned repository evidence, then assign labels. Static files
that describe a command are never evidence that the command ran; see
[Review conditions](../ANNOTATION_GUIDE.md#review-conditions).

Follow the order in [Decision order](../ANNOTATION_GUIDE.md#decision-order):
precondition, then correctness, then utility, then root cause, then
verification mode, confidence, evidence, and notes. Assigning root cause before
correctness tends to produce a root cause that explains a defect you have not
yet established.

## The three judgements are independent

[Correctness](../ANNOTATION_GUIDE.md#correctness),
[applicability](../ANNOTATION_GUIDE.md#applicability), and
[utility](../ANNOTATION_GUIDE.md#utility) are separate fields and must not be
collapsed. A correct finding can be low-value. A rule can be inapplicable even
when its message names a real file. An `unknown` result can be correct when the
required evidence is not obtainable under the static, offline contract the
analyzer runs under.

Three rules keep them apart:

- Decide correctness against the rule contract, not against your own view of
  what the rule should have checked. A `pass` needs affirmative support; the
  mere absence of a detected problem is not support.
- Decide applicability from evidence that the precondition holds or is absent.
  An unfamiliar repository layout is not evidence of absence.
- Decide utility from what an author or reviewer would do next. Severity is not
  utility: a severe category can be low-value when the message is
  non-specific, and a low-severity correction can be actionable.

Abstentions and suppressed records are reviewed like any other, and both parts
of a suppressed record are judged — the underlying decision, and whether the
suppression was right. See
[Analyzer abstentions and suppressed findings](../ANNOTATION_GUIDE.md#analyzer-abstentions-and-suppressed-findings).

## Root cause and verification mode

Record one primary root cause, and choose the earliest cause the evidence
supports rather than the last visible symptom. The operational definitions are
tabulated in
[Primary root cause](../ANNOTATION_GUIDE.md#primary-root-cause). Two points are
easy to miss: `none` is the right value for a correct and appropriately
presented result, and root cause describes an analyzer or evidence limitation,
never the quality of the repository under review.

[Verification mode](../ANNOTATION_GUIDE.md#verification-mode) records what
supplied the decisive evidence. `dynamic` is out of scope for this pilot. A
finding that cannot be decided without executing the repository stays `unclear`
with `needs_dynamic_evidence`; do not run the repository to break a tie. Online
and author evidence can establish review ground truth, but they do not turn the
analyzer's static observation into evidence of execution.

## Evidence, confidence, and notes

Every decision needs at least one evidence link, and it must support the
decision itself; a locator that only identifies the repository is not enough.
Follow the preference order in
[Evidence and confidence](../ANNOTATION_GUIDE.md#evidence-and-confidence).
`label_confidence` records your confidence in the annotation, not the
analyzer's confidence, and `1.0` is reserved for direct and unambiguous
evidence. Every `unclear` decision needs notes naming the missing or
conflicting evidence. Record conflicting evidence rather than choosing the
convenient source, and never place a credential, token, private URL, or secret
value in a link or a note.

## Declarations and recusal

The rules are the same as for the claim gate and are stated in
[Conflict of interest and recusal](../ANNOTATION_GUIDE.md#conflict-of-interest-and-recusal),
with one difference in scope: a finding-review declaration is bound to your
assigned repository set and the finding-fingerprint-set digest, not to a single
repository and artifact. Both the blinding declaration and the
conflict-of-interest declaration must be recorded before your first decision.
If any affirmation cannot be made, stop; the complete role-bound file is
reassigned. Declarations carry booleans and assignment scope only.

## Calibration findings come first

Your file contains a predeclared calibration allowlist of 40 Layer B records
that both reviewers annotate. Complete only that set first, then stop and wait.
Calibration exists to expose disagreements caused by the handbook rather than
by the evidence, and it is checked before the rest of the workload begins:
correctness and applicability each need at least 80% exact agreement before
either reviewer continues. If the gate fails, the handbook is versioned and a
fresh allocation is frozen under a dated amendment before the work restarts.

The 40 calibration items are calibration work. They are not an effectiveness
estimate, and their agreement figure is not a result.

## The second-review quota

The two files are role-bound and unequal. The primary file covers the complete
Layer B population. The secondary file covers exactly the deterministic
second-review allocation: the larger of 40 records or 20% of all fresh Layer B
review targets, rounded up, stratified by repository and by emitted, pass and
abstention state. The 40 calibration findings are inside that quota rather than
added to it. Stress records belong to neither assignment and never enter an
effectiveness denominator.

Review only the fingerprints allocated to your role and declared phase. Your
file omits cohort assignments and every other reviewer's decisions, and the
entry command cannot display data the file does not contain. If you find
yourself inferring a cohort, stop and ask the coordinator.

## Independence and adjudication

Second review is independent. Do not discuss an item, and do not inspect the
other reviewer's labels, before both records are saved. Original records are
never overwritten.

A disagreement in correctness, applicability, or utility is resolved by an
independent adjudicator who was not an initial reviewer, working from both
decisions and the underlying evidence, and recording a separate resolution with
its own evidence, confidence and rationale. The adjudicator may keep `unclear`;
a disagreement is not resolved by forcing certainty. Differences in root cause
or verification mode stay visible as secondary disagreements. See
[Disagreement and adjudication](../ANNOTATION_GUIDE.md#disagreement-and-adjudication).

## Entering decisions

Decisions go into your own role-bound file through
`corpus/scripts/label_findings.py review-source`, with `--review-set
calibration` for the first phase and `--review-set remaining` afterwards. The
command takes the frozen allocation, the candidate run, both pristine sample
sources and your review file, and it revalidates the binding on every
invocation. Automated checks may confirm structure and source identity; they
are not reviewer decisions, and a tool's suggestion must never be recorded
under your identifier.

## What these labels can and cannot support

Reviewed proportions from this sample are descriptive summaries of the reviewed
records. They are not corpus rates, and they are not an accuracy estimate for
any population. Every aggregate is reported with its repository and review
counts. Small-sample kappa values are descriptive diagnostics and never stand
in for the agreement counts themselves.

Once these labels inform a detector change, the pilot becomes a development
set: same-commit before/after runs are paired diagnostic comparisons, not
unbiased accuracy estimates. A generalized performance claim requires a
separate confirmatory holdout, frozen with its inventory, hypotheses, sampling
plan and acceptance rules fixed before its results are inspected. See
[Reuse after detector changes](../ANNOTATION_GUIDE.md#reuse-after-detector-changes).
