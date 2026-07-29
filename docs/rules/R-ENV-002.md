# R-ENV-002 — Lockfile capturing the transitive environment

**Category:** Environment & Tooling  
**Severity:** medium  
**Weight:** 3

## Why it matters

Direct pins can leave transitive package dependencies floating. A lockfile
records one resolved package dependency set, but it does not capture the host
operating system, drivers, hardware, or undeclared external libraries.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-002"]`

Suppressed findings still appear in reports, marked as ignored.
