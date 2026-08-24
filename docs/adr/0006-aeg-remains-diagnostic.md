# 6. The evidence graph remains a diagnostic subsystem

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

Design documentation described the artifact evidence graph as the intermediate
representation that analysis flows through. It is not. `engine.py` contains no
reference to it. A check runs scan, collect, rules, score, and then builds the
claim graph — a different structure. The evidence graph's only production path is
the `adduce graph` command, and [aeg-schema.md](../aeg-schema.md) already states
that building it changes nothing about a check.

Two further capabilities are declared but unreachable. Producer ownership
validates a `builtin` or `plugin:<name>` string and raises on conflict, but the
built-in producer list is a fixed pair and no `plugin:*` owner is ever
constructed anywhere. The stored-graph major-version check is called only from
the read path, and nothing in the shipped code reads a stored graph.

What is real and enforced: the node and edge confidence ceiling, validated from
both constructors; retain-and-ignore handling of unknown types; and producers
emitting 10 of the 19 declared node types.

## Decision

Ship the evidence graph as what it is — a diagnostic subsystem reached through
`adduce graph` — and document its state accurately.

Do not wire it into the check pipeline in order to make an older architecture
document true. Wider integration is worth doing when something needs it for
correctness, not because a diagram asserts it.

Correct the documentation instead:

- the graph is **partially implemented**: real schema, real validation, real
  producers, not on the check path;
- plugin producer ownership is **proposed**, not implemented — the string format
  exists, the capability does not;
- broader pipeline integration is **deferred**.

Keep the three layers distinct. The evidence graph holds artifact facts and
provenance; the claim graph and claim trail hold scientific-claim provenance;
findings and their items hold rule verdicts and observations. Three questions,
three structures, no duplication.

### Alternatives considered

**Routing `run_check` through the graph now,** to match the documentation.
Rejected: it is a large change to the hot path, justified only by a document, and
the documentation is the thing that is wrong.

**Deleting the unreachable version check and ownership validation.** Rejected.
Both are correct and cheap, and both are needed the moment anything reads a
stored graph or a plugin produces nodes. They are latent, not dead.

## Consequences

The architecture diagram changes. A diagram showing the graph as the spine of the
pipeline is wrong, and correcting it is cheaper and more honest than building
toward it under release pressure.

Node types with no producer stay declared. The vocabulary is the schema, and an
unproduced type is a documented gap rather than dead code — but it must be
labelled as such.

Claim extraction runs on the ordinary check path for any repository without an
author-written manifest, for drafting and display only, and never reaches the
score. Documentation should say that precisely: "separate from scoring" is true,
"off the pipeline" is not.
