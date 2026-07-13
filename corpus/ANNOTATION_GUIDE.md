# Finding annotation handbook

This handbook defines the review decisions used by the Adduce validation
pilot. It applies to one sampled finding at one pinned repository commit. It
does not turn the purposive pilot into a population sample or make its
descriptive proportions generalized accuracy estimates.

## Review conditions

Review only a sample that passes `label_findings.py` validation against its
immutable run and the frozen sampler source and Python identity. Use stable,
non-personal reviewer identifiers that remain attributable within the study.
Correctness, applicability, utility, claim verification, and adjudication are
human judgements; automated checks or suggestions cannot be recorded as
reviewer decisions. Initial reviewers work independently and do not inspect
cohort labels, badge status, another reviewer's decisions, or aggregate results
before submitting their own review.

Judge the complete analyzer decision: status, scope, message, locations, and,
when present, suppression. Read the documented rule contract and inspect the
pinned repository evidence before assigning a label. Do not infer that a
repository command ran merely because static files describe it.

## Decision order

Assign the fields in this order:

1. Determine whether the rule's documented precondition applies.
2. Determine whether the analyzer decision matches the available evidence.
3. Assess the practical value of the decision to an artifact author or
   reviewer.
4. Record one primary root cause for any material defect.
5. Record the verification mode, confidence, evidence links, and notes.

Correctness, applicability, and utility are independent. A correct finding can
be low-value; a rule can be inapplicable even when its message describes a real
file; and an `unknown` result can be correct when the required evidence is not
available.

## Correctness

- `correct`: The status and central assertions are supported at the pinned
  commit, the scope and locations identify the relevant evidence, and no
  material association is known to be wrong. A `pass` must have affirmative
  support under the rule contract; absence of a detected problem is not enough
  by itself.
- `incorrect`: A material status, fact, scope, location, or association is
  contradicted by the evidence. This includes a false pass, a failure caused by
  missing a documented semantic equivalent, an unjustified abstention, or a
  suppressed result that should have remained visible.
- `unclear`: The prescribed review cannot determine correctness from the
  accessible pinned evidence. Use this only after recording what was checked
  and what evidence is missing; it is not a substitute for a difficult review.

Minor wording defects that do not alter the central assertion may remain
`correct` and receive `minor` or `low_value` utility with `wording_problem` as
the root cause. If the wording would lead a reasonable user to a wrong
conclusion, label correctness `incorrect`.

## Applicability

- `applicable`: The repository, claim, workflow, language, or artifact type
  satisfies the documented rule precondition.
- `not_applicable`: Evidence establishes that the precondition is absent. Do
  not use this merely because a repository uses an unfamiliar layout or the
  expected evidence was not found.
- `unclear`: The available evidence cannot establish whether the precondition
  holds.

Judge the claim-relevant workflow when a repository contains several examples,
packages, or historical implementations. Evidence in tests, vendored code,
generated output, or an unrelated subproject does not automatically establish
applicability to the reported result.

## Utility

- `actionable`: The decision identifies a specific, feasible change that would
  materially improve claim traceability, reproducibility evidence, or reviewer
  efficiency.
- `minor`: The decision is valid and reasonably specific, but the likely
  benefit or risk reduction is limited.
- `low_value`: The decision is technically defensible but vague, duplicative,
  disproportionate, or unlikely to change an author or reviewer decision.
- `not_applicable`: A utility judgement is not meaningful because the rule is
  demonstrably outside scope.
- `unclear`: The likely value cannot be determined from the available context.

Do not equate severity with utility. A severe category can be low-value when
the message is non-specific, while a low-severity correction can be actionable.
Repeated findings may be individually correct but low-value when they report
the same remedy without adding evidence.

## Analyzer abstentions and suppressed findings

Review all sampled statuses, including suppressed records.

- `unknown` is correct only when the rule requires evidence that cannot be
  resolved under the stated static, offline contract. It is incorrect when the
  pinned artifact contains sufficient evidence that the collector or rule did
  not recognize.
- `not-applicable` is correct only when the rule precondition is demonstrably
  false. Lack of a familiar filename is not sufficient.
- `partial` is correct when the available evidence supports only part of the
  documented condition or contains an unresolved conflict. It is incorrect
  when the evidence is sufficient for a determinate pass or fail.
- `pass` must be reviewed for missed conditions; `fail` and `partial` must be
  reviewed for false alarms and semantic equivalents.
- For a suppressed record, judge both the underlying decision and whether the
  suppression is justified. Use `suppression_policy` as the primary root cause
  when the visibility decision is the material defect.

## Primary root cause

Choose the earliest primary cause supported by the evidence, not every
downstream symptom.

| Value | Operational definition |
| --- | --- |
| `collector_miss` | Relevant static evidence exists but collection did not capture it. |
| `semantic_equivalence` | Equivalent evidence or syntax was collected but not recognized as satisfying the rule. |
| `abstraction_limit` | The required relationship cannot be represented or resolved by the current model. |
| `repository_context` | The rule applied the wrong workflow, scope, generated/vendor boundary, or repository-specific context. |
| `wording_problem` | The underlying determination is usable, but its title or message is materially misleading or non-specific. |
| `weighting_problem` | Detection is acceptable, but severity, category contribution, or score effect is disproportionate. |
| `real_repository_gap` | A common repository structure is unsupported and no more specific cause above applies. |
| `needs_dynamic_evidence` | Static evidence is inherently insufficient and execution is required to decide. |
| `needs_author_input` | The artifact does not contain the information and only the author or maintainer can resolve it. |
| `suppression_policy` | The underlying determination may be sound, but automatic visibility or suppression is wrong. |
| `none` | No material defect is identified. |

Use `none` for a correct, appropriately presented result. Root cause describes
the analyzer or evidence limitation; it is not a judgement about repository
quality.

## Verification mode

| Value | Required basis |
| --- | --- |
| `manual_static` | Inspection of the pinned repository, frozen claim source, or other versioned local artifact without execution. |
| `manual_online` | Inspection of an authoritative public source; record the exact URL and, when material, a dated or hashed local snapshot. |
| `dynamic` | Out of scope for this initial pilot. A later protocol must predeclare an isolated environment, credential and network policy, resource limits, command, inputs, and retained output before this mode is used. |
| `author_confirmed` | A versioned, attributable author or maintainer statement; record its date and locator. |

Choose the mode that supplied the decisive evidence. If several modes were
needed, record the strongest decisive mode and list the others in notes.
Dynamic verification is not performed in this initial pilot; a finding that
cannot be decided without execution remains `unclear` with
`needs_dynamic_evidence`. Online and author evidence may establish review
ground truth, but they do not convert Adduce's static observation into proof of
execution.

## Evidence and confidence

Every review and adjudication requires at least one evidence link. Prefer
durable locators in this order:

1. pinned repository path and line or object locator;
2. hashed local paper, result, configuration, or run-output snapshot;
3. authoritative URL with the relevant version and date;
4. retained dynamic-run or author-confirmation record.

Evidence must support the decision, not merely identify the repository. Record
conflicting evidence rather than choosing the convenient source. Never include
credentials, tokens, private URLs, or copied secret values in links or notes.

`label_confidence` records confidence in the annotation, not Adduce's own
confidence. Use `1.0` only when the decisive evidence is direct and
unambiguous. Every `unclear` decision requires notes naming the missing or
conflicting evidence. An adjudication requires a concise rationale even when
the adjudicator agrees with one reviewer.

## Disagreement and adjudication

Second review is independent: reviewers do not discuss an item or inspect one
another's labels before both records are saved. The original records are never
overwritten.

A disagreement in correctness, applicability, or utility requires an
independent adjudicator who was not an initial reviewer. The adjudicator
inspects both decisions and the underlying evidence, then records a separate
resolution, evidence links, confidence, and rationale. The adjudicator may
retain `unclear`; disagreement is not resolved by forcing certainty.

Differences in root cause or verification mode remain visible as secondary
disagreements and may be discussed in the report. Report agreement counts and
the number of compared items with any agreement statistic. Small-sample kappa
values are descriptive diagnostics, not evidence of annotation validity.

## Reuse after detector changes

Once pilot findings have been inspected and used to change detectors, this
pilot is a development set. Same-commit before/after runs are paired diagnostic
comparisons, not unbiased accuracy estimates. Before publication or any
generalized performance claim, freeze a separate confirmatory holdout with its
inventory, hypotheses, sampling plan, and acceptance rules fixed before its
results are inspected.
