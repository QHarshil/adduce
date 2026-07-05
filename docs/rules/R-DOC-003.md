# R-DOC-003 — Expected outputs and results stated

**Category:** Documentation  
**Severity:** medium  
**Weight:** 4

## Why it matters

Reproduction needs a target: the numbers (and tolerance) a rerun should land on. Without them a reproducer cannot tell success from failure.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DOC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DOC-003"]`

Suppressed findings still appear in reports, marked as ignored.
