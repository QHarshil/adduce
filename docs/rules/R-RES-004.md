# R-RES-004 — Reported metric has no corresponding logged result

**Category:** Result Reconciliation  
**Weight:** 3

## Why it matters

A number with no log behind it is exactly what artifact reviewers probe first.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RES-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RES-004"]`

Suppressed findings still appear in reports, marked as ignored.
