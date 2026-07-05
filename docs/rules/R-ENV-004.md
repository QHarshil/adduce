# R-ENV-004 — Python version specified

**Category:** Environment & Tooling  
**Severity:** medium  
**Weight:** 3

## Why it matters

Results and even installability differ across interpreter versions.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-004"]`

Suppressed findings still appear in reports, marked as ignored.
