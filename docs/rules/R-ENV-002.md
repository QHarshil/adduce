# R-ENV-002 — Lockfile capturing the transitive environment

**Category:** Environment & Tooling  
**Weight:** 3

## Why it matters

Direct pins still leave transitive dependencies floating; a lockfile freezes the entire resolved environment.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-002"]`

Suppressed findings still appear in reports, marked as ignored.
