# 9. Developmental measurements are withheld while the corpus interval is unlocked

- **Status:** Accepted
- **Date:** 2026-08-31
- **Amends:** [ADR 0005](0005-shipping-capability-while-validation-continues.md)

## Context

[ADR 0005](0005-shipping-capability-while-validation-continues.md) requires a
release made under its policy to preserve the current measurements as
developmental status rather than restating them as results. That was the right
instruction while those measurements were dated observations standing against a
live preregistration lock.

Protocol amendment 8 (`corpus/PILOT_PROTOCOL.md`, 2026-08-28) retired the `r6`
lock and opened an unlocked development interval. Its reporting restriction is
absolute for the duration: no effectiveness, calibration, false-positive,
score-separation, badge-prediction or calibrated-tier figure may be stated,
published, or carried into a release note while the interval is open. The pairs
the existing figures were drawn from are also no longer reproducible, because
the analyzer they were measured against is being rebuilt on purpose, which is
why the interval exists.

A maintainer following ADR 0005's fourth bullet literally would therefore
reintroduce figures the amendment forbids and the tree cannot reproduce. The two
instructions cannot both be followed, and the amendment is the later and
narrower record.

## Decision

Amendment 8 governs. While the unlocked development interval is open, a release
made under ADR 0005 preserves the verdict and the status, and states no figure
the amendment restricts.

A release note written under this policy:

- names which validation remains developmental, and what is therefore not yet
  supported by evidence;
- carries an unmet acceptance criterion as unmet, without softening it and
  without a number;
- states no effectiveness, calibration, precision, recall, false-positive,
  score-separation, badge-prediction or calibrated-tier figure;
- deletes a figure it cannot reproduce from the tree it ships, rather than
  dating it.

The rest of ADR 0005 stands unchanged, including its refusal to make a partial
effectiveness claim from partial measurements — this record narrows one bullet,
it does not weaken the policy. The instruction to preserve measurements resumes
when a successor lock is registered under a dated amendment and the interval
closes, at which point the figures are measured against the finished analyzer
rather than carried forward.

### Alternatives considered

**Editing ADR 0005's fourth bullet.** Rejected: a record is not edited to match
later reality, and that bullet was correct under the conditions it was written
for. A reader needs to see both the earlier instruction and what displaced it.

**Reading "preserve" as "restate the earlier figures with a disclaimer".**
Rejected. Amendment 8 admits no such figure in a release note at all, and a
disclaimer does not make an unreproducible number reproducible.

## Consequences

A release note written inside the interval carries fewer numbers than one
written outside it, and deliberately so. The absence is not a gap in the record:
the record is the protocol amendment and the four executable assertions that
replaced the lock.

Engineering measurements are unaffected. Timings, byte sizes and counts taken
from the tree being shipped are measurements of the software rather than of its
effectiveness, and they keep their dated provenance blocks.

This record expires by its own terms. When the interval closes, ADR 0005's
fourth bullet applies again as written, and a successor record is needed only if
the policy itself changes.
