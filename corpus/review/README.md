# Review guidance

Operational guidance for the two human review gates of the Adduce validation
pilot: the blinded claim review, and the later finding review. These documents
tell a reviewer or a coordinator how to carry out work that the tracked
protocol artifacts define.

## Authoritative sources

These four files govern. Everything in this directory is derived from them.
Where a document here and a tracked protocol artifact disagree, the tracked
artifact governs and the document here is wrong.

- [`../PILOT_PROTOCOL.md`](../PILOT_PROTOCOL.md) — the preregistered protocol,
  its dated amendments, the ground-truth and review requirements, the decision
  rule, and the reporting limits.
- [`../ANNOTATION_GUIDE.md`](../ANNOTATION_GUIDE.md) — the operational
  definitions for finding review, the conflict-of-interest and recusal rules,
  and the evidence-preference order.
- [`../claim-review.schema.json`](../claim-review.schema.json) — the required
  fields and the exact decision vocabulary for claim review.
- [`../finding-review.schema.json`](../finding-review.schema.json) — the
  required fields for independent and merged finding review.

## Reviewer-facing documents

Place these in a reviewer packet. They contain no expected results, no
aggregate distributions, and no Adduce output.

| Document | Use |
| --- | --- |
| [`REVIEWER_GUIDE.md`](REVIEWER_GUIDE.md) | The claim-review gate, end to end: the decisions, the vocabulary, the declarations, the entry tool. |
| [`REVIEWER_CHECKLIST.md`](REVIEWER_CHECKLIST.md) | The same gate as a working checklist, from setup to return. |
| [`FINDING_REVIEW_GUIDE.md`](FINDING_REVIEW_GUIDE.md) | The later finding-review gate. Not used until its prerequisites are met. |

## Coordinator-only documents

| Document | Use |
| --- | --- |
| [`EFFECTIVENESS_METRICS.md`](EFFECTIVENESS_METRICS.md) | Every evaluation dimension, its numerator and denominator, and when it becomes available. |

`EFFECTIVENESS_METRICS.md` is never placed in a reviewer packet. It describes
what the completed reviews are used to compute, which is information a blinded
reviewer is not meant to hold while deciding.

## Standing limits

Adduce reports detected signals. Nothing in this directory supports a
certification of reproducibility, a population false-positive rate, or a
calibrated score threshold. The permitted conclusions at any point in the
protocol are stated in [`../PILOT_PROTOCOL.md`](../PILOT_PROTOCOL.md) under
"Reporting limits", and they narrow rather than widen as amendments are added.
