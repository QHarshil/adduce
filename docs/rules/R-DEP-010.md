# R-DEP-010 — Imported but undeclared (ghost) dependencies

**Category:** Dependencies  
**Weight:** 4

## Why it matters

Code that imports a package no manifest declares runs only on machines where it happens to be installed — the canonical 'works here, breaks there'.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-010`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-010"]`

Suppressed findings still appear in reports, marked as ignored.
