# 5. Shipping capability while empirical validation continues

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

adduce makes two different kinds of claim. One is engineering: the tool detects
the signals it documents, its output contracts are stable, its extension points
work. The other is empirical: the tool's findings correspond to what a human
reviewer would conclude, measured against a preregistered analysis plan with a
locked corpus and independent human review.

Those two claims are validated by completely different means and on completely
different clocks. Engineering claims are settled by tests, gates and review, in
days. The empirical claim is settled by a preregistered study whose elapsed time
is dominated by human reviewers working through hundreds of adjudications, and
whose lock must be regenerated after the last analyzer change.

If a release cannot ship until the empirical claim is closed, then every piece
of finished, useful engineering waits on a research schedule. External developers
building against the package are blocked by work that has nothing to do with the
API they depend on.

## Decision

A release may ship productized, validated capability while empirical validation
continues separately.

A release made under this policy:

- makes **no** final effectiveness claim;
- carries **no** preregistration lock, and does not regenerate one;
- states explicitly which validation remains developmental, and what is
  therefore not yet supported by evidence;
- preserves the current measurements as developmental status rather than
  restating them as results, and does not weaken an unmet acceptance criterion to
  make the release look complete.

The preregistration lock, its amendment, and the human-review gates belong to the
release that actually makes the effectiveness claim.

Capability is the release criterion, never authorship. A gate is a working,
tested capability with a documented contract; who implements it does not enter
into it.

[docs/releasing.md](../releasing.md) already permits this shape: its first gate
reads "complete the version's corpus and human-review gates, **or document
explicitly which validation remains developmental**." This record makes that
option a deliberate policy rather than an escape hatch.

### Alternatives considered

**Holding every release until the study closes.** Rejected: it couples an
external developer's dependency to reviewer availability, and it gives no route
to shipping a correctness fix.

**Making a partial effectiveness claim from partial measurements.** Rejected
outright. A preregistered study exists precisely so that results are not reported
from whatever subset happens to be finished, and quoting interim numbers as
results would defeat it.

## Consequences

Documentation carries the weight. If a release ships without the empirical claim,
the honest-limits language is what stops a reader inferring one, so it is a
release gate rather than a courtesy.

Subsystems must be labelled by their real state — implemented, partially
implemented, proposed, deferred, or rejected by measurement — because a reader
cannot otherwise tell finished capability from an intention.

Measured dead ends are release artifacts. An experiment that was tried and
rejected on evidence has to stay recorded with its measurements, or it gets
rediscovered and re-argued.
