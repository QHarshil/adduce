# 1. Status applicability and assessment coverage

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

`Status` has five members. `PASS`, `PARTIAL` and `FAIL` carry quality values of
1.0, 0.5 and 0.0. `UNKNOWN` and `NOT_APPLICABLE` both carry `None`.

Because they share that `None`, every scoring path that tests `score_value is
None` treats them as one state. They are not one state. `NOT_APPLICABLE` means
the check does not apply to this repository; `UNKNOWN` means the check applies
and adduce could not determine the answer. Substituting either for the other
changes no number adduce currently reports.

Two defects follow, in different layers:

- A category whose findings produce no assessed weight is dropped from the score
  card before any reporter sees it, so the terminal, Markdown, LaTeX and JSON
  outputs lose it identically. A category that is entirely `NOT_APPLICABLE`
  should be omitted; one that is entirely `UNKNOWN` should not.
- Terminal output renders a category's note as "all detected checks satisfied"
  whenever it holds no `PARTIAL` and no `FAIL`. A category holding `PASS` and
  `UNKNOWN` therefore claims everything is satisfied, which is false.

There is also a second, separate not-applicable mechanism. A rule whose
`applies_to` returns `False` produces no `Finding` at all, so it never reaches a
numerator or a denominator. On adduce's own repository nine rules are skipped
that way, against 16 `NOT_APPLICABLE` and 2 `UNKNOWN` in a set of 69 findings.

## Decision

Keep all five statuses, and separate applicability from assessment:

| Status | Applicable | Assessed | Quality value |
| --- | --- | --- | --- |
| `PASS` | yes | yes | 1.0 |
| `PARTIAL` | yes | yes | 0.5 |
| `FAIL` | yes | yes | 0.0 |
| `UNKNOWN` | yes | no | none |
| `NOT_APPLICABLE` | no | no | none |

`score_value` may stay `None` for both, but no scoring code may infer
applicability from it.

Assessment coverage is count-based:

```
applicable = PASS + PARTIAL + FAIL + UNKNOWN
assessed   = PASS + PARTIAL + FAIL
coverage   = assessed / applicable
```

Weights continue to apply to the quality score and not to coverage. Coverage
answers "what fraction of applicable checks reached an assessment?", which is a
question about checks rather than about importance.

Rules skipped by `applies_to` stay outside the result set and both denominators,
and their count is reported separately alongside the outcome counts. Four
outcomes must remain distinguishable: skipped before evaluation, evaluated and
not applicable, applicable but unassessed, and applicable and assessed.

Fix the two defects in their own layers. The vanishing category is a scoring
defect; the misleading note is a reporting defect. Fixing the scoring defect in
reporters is not acceptable, because four output paths share it.

### Alternatives considered

**A four-status model,** merging `UNKNOWN` into `NOT_APPLICABLE`, would make the
arithmetic trivial. Rejected: it destroys the distinction between "does not
apply" and "could not tell", which is the more useful of the two for a reader
deciding whether to trust a result.

**Weighted coverage,** `assessed weight / applicable weight`. Deferred rather
than rejected. On adduce's own repository it differs from the count-based figure
by 1.3 percentage points (96.2% against 97.5%). Weights span 1 to 8, so a
repository whose unassessed checks are its heaviest would diverge further; the
divergence across the corpus is worth measuring before adding a second coverage
number.

## Consequences

Coverage on adduce's own repository moves from 73.9% to 96.2%. **This is a
denominator correction, not an improvement in analysis.**

```
old:  51 assessed / 69 returned findings   = 73.9 %
new:  51 assessed / 53 applicable findings = 96.2 %
```

The 16 `NOT_APPLICABLE` findings never applied to the repository and should not
have reduced coverage. adduce does not assess 22 percentage points more evidence
than it did before. Release notes and the scoring documentation must say so
plainly.

An existing test asserts the old semantics over a fixture holding one
`NOT_APPLICABLE` and one `UNKNOWN`. It is correctly named for the model it locks,
so it changes when the model changes. That is a semantics change rather than a
loosened assertion.

Preserving an unassessed category must not admit its weight to the quality
score's denominator. Doing so would let a category adduce could not assess drag
the total down as though it had failed, which is the failure mode this record
exists to prevent.

Expressing "no assessed information" distinctly from a failing zero is a
separate change to the score card's public shape, and belongs with the coverage
semantics rather than with the two defect fixes.
