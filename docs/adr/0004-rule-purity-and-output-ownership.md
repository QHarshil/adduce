# 4. Rule purity and output ownership

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

Rules are meant to read typed evidence and return a verdict: no creating,
updating or deleting files, no executing repository code, no subprocesses, no
network calls, no mutating evidence or the repository.

That holds today. Across all 78 built-in rule implementations, no rule module
imports or calls a filesystem, subprocess or network API, no rule mutates
evidence, and no `applies_to` performs I/O.

It holds by convention rather than by construction. `Evidence.repo` is a live
repository object whose `read_text` performs real disk I/O. Built-in rules touch
only in-memory members, but a third-party rule can read files from inside
`evaluate` and neither the plugin loader nor the type checker will notice.

Separately, integrations have written sidecar report files from inside
`evaluate` — not out of disregard for the contract, but because the API had
nowhere to put per-item detail.

## Decision

Keep the purity contract, and describe it accurately.

Report persistence belongs outside `Rule.evaluate`. If adduce writes a detailed
report, the report or engine layer owns the write. A rule returns data. The child
result model in ADR 0002 removes the reason integrations had to write sidecars.

**Do not describe third-party plugins as sandboxed.** Python entry points import
plugin packages, so installing a plugin grants that package normal process
privileges. The purity contract is an API and design rule, not a security
boundary. The documentation must say this plainly: install plugins you trust, and
adduce's documented trust model bounds its own analysis rather than arbitrary
third-party code.

### Alternatives considered

**Enforcing purity by construction,** passing rules a read-only or fully
in-memory repository view. This is the change that would make purity real for
third-party code. Not taken yet: it alters the evidence contract every rule
depends on, and the built-in rules that would validate it already comply. Named
here so it is a known option rather than a later discovery.

**Sandboxing plugin execution.** Rejected for now. Meaningful isolation of
arbitrary Python needs process or interpreter separation, which is a large piece
of work, and claiming a sandbox we do not have would be worse than claiming
none.

## Consequences

A reader who believes plugins are sandboxed makes worse decisions than one who
knows they are not, so the honest framing is also the useful one.

`Evidence.repo.read_text` reachability is the surface to close if purity is ever
to bind plugins rather than describe built-in practice.

Untrusted content stays data. Finding and item text may come from an untrusted
repository or plugin, so reporters escape it, never interpolate it into a shell
command, never treat an attribute as a path to open, and never let it trigger
network access.
