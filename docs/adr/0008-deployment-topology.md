# 8. Deployment topology

- **Status:** Accepted
- **Date:** 2026-08-28

## Context

adduce has been described in working notes as "a scanner, not a platform". That
phrase conflates three separable questions:

- **execution locality** — where an audit runs
- **software extensibility** — whether third parties can add rules and reporters
- **hosted infrastructure** — whether the project operates a service

The project already answers the second in the affirmative. There is a
documented extension API under ADR 0003, an `adduce.rules` entry-point group, an
`adduce.reporters` group, and a contract suite that exercises a real installed
distribution in continuous integration. A record saying "not a platform" would
contradict the shipped surface, and would have to be reversed the first time a
rule pack shipped.

## Decision

**The core is local-first and offline-capable.** An audit runs where the
artifact is: a developer machine, a continuous-integration job, a reviewer's
checkout. Offline by default. The online resolution and dynamic execution layers
are separately fenced and opt-in. No audit requires a network service.

**Extensibility through supported APIs is part of the core architecture, not an
extension of it.** Rule packs and reporters are a public surface with a
stability contract under ADR 0003 and a contract suite that runs against a real
installation. Third-party packs are trusted in-process Python and are explicitly
not a security boundary; containment exists so that one misbehaving pack cannot
discard an audit, which is a reliability property and not a sandbox.

**Hosted orchestration, persistent multi-repository services and editor or LSP
integration are not required by the core and are not being built.** Nothing in
the design forecloses them; if they are ever built, they are built on the CLI
and the JSON report.

## Reconsideration criteria

These are not unfinished decisions. They are the conditions under which this
decision should be revisited, and the measurement that would settle each. No
threshold is preregistered, because no evidence yet justifies a particular one.

**Hosted orchestration.** Reconsider when multiple independent users demonstrate
longitudinal or large multi-repository workflows that local or process-level
orchestration cannot reasonably satisfy. What would settle it: a stated demand,
and a measurement of what an audit costs at the scale requested.

**Editor and LSP integration.** Reconsider when finding-location quality is
measured across representative repositories and enough actionable findings carry
stable source positions to make interactive diagnostics useful. What would
settle it: the location-bearing fraction of actionable findings measured across
the fifteen pinned repositories, reported overall and per rule category.

The observation that motivated caution is recorded here as the reason the
question was asked, not as evidence for the answer it received. Measured on
2026-08-28 against source tree
`cae7dd33dd0077b5ecc4fe805ad707bd49e19bd2556a76204d494a5ea36ec8dd`, a self-scan
of this repository reports **7 of 69** findings carrying a location, and 7 of
the 30 findings that are actionable. That is a single self-referential
observation on one repository, and it is not evidence for a permanent
architectural position.

## Consequences

The extension API stays a first-class surface with a stability contract, and
changes to it remain governed by ADR 0003. The core keeps no dependency on a
hosted component and no assumption that one exists.

"Not a platform" is retired as project shorthand. It is not usable without
saying which of the three questions it answers, and on the extensibility
question it states the opposite of what the project ships.
