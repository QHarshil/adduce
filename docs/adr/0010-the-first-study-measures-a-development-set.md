# 10. The first human study measures a development set

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

The plan for 0.3 has been to finish claim resolution, freeze the analyzer,
register a successor preregistration lock, and run the blinded human review over
the fifteen pinned repositories. The implicit expectation was that this produces
the effectiveness result a paper reports.

It cannot, and the protocol already says so. `corpus/PILOT_PROTOCOL.md` states:

> Once findings from this pilot inform a detector change, the pilot is a
> development set. Its before/after measurements are paired diagnostics, not an
> unbiased accuracy estimate. Corpus expansion must freeze a separate
> confirmatory holdout before its results are inspected; publication or any
> generalized performance claim depends on that holdout rather than the reused
> pilot.

Pilot findings have informed detector changes repeatedly. Claim extraction was
measured against those pairs and then improved against what the measurement
showed, which is the definition the clause gives. That happened before 0.2.0 and
is not undone by registering a new lock: a lock binds what the analyzer is at a
moment, and says nothing about which observations shaped it.

Two further properties of the frozen inventory bound what the fifteen can
support at all. `corpus/repos.csv` carries three cohorts of five, and every
badged repository holds the same badge set, so the inventory contains no
variation in evaluated outcome. The protocol separately excludes stress findings
from both effectiveness assignments. A study over this inventory therefore
cannot relate a score to whether an artifact reproduces, however it is analysed.

## Decision

The first human study is a development-set study, and says so.

It measures agreement between independent reviewers on a prepared
claim-to-artifact record, the abstention behaviour of the analyzer, a failure
taxonomy for the cases reviewers mark as needing revision, determinism, and the
reviewer time and friction the workflow costs. Those are properties of this
system on this material, reported as such.

It states no generalized accuracy, precision, recall or false-positive rate, and
no relationship between a score and a reproduction outcome. A paper drawn from
it names the development-set status in the claim itself rather than in a
limitations paragraph.

A confirmatory result requires a separate holdout, frozen before any of its
results are inspected, and carrying variation in evaluated outcome. That is a
later study and a later corpus, sized from the reviewer cost this one measures.

## Consequences

The successor lock is still worth registering, and for the reason it always was:
it fixes what the analyzer is when the humans look at it, so the agreement and
abstention figures name a specific system. It is not what converts a development
set into a confirmatory one.

Reviewer burden becomes a first-class result rather than logistics. The workflow
rehearsal already established that a ten-claim assignment costs one hundred and
ten decisions, each requiring a decision, a rationale and evidence, across
roughly one hundred and fifty command invocations. Reporting that honestly is
part of what the study contributes, and it is the input that sizes the holdout.

One design question is now on the critical path rather than after it. The
instrument shows the reviewer the record's expected resolution and asks whether
it is correct, so it measures verification of a prepared record rather than
independent derivation of the links. The reviewer feedback schema already
carries `felt_pressure_to_verify`, so the anchoring risk is instrumented. If a
future study wants the stronger independent-derivation claim, the expected
resolution has to be withheld, and that is a change to make before a lock is
registered rather than after.

Nothing here weakens ADR 0005 or ADR 0009. Capability still ships while
validation continues, and no restricted figure is stated while the interval is
open. This record says what the study that ends the interval can and cannot
conclude.
