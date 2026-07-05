# R-EXEC-001 — Discoverable entrypoint

**Category:** Code & Execution  
**Weight:** 5

## Why it matters

Without an obvious entrypoint, reproduction starts with reverse-engineering which of the scripts is the one that produced the results.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-EXEC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-EXEC-001"]`

Suppressed findings still appear in reports, marked as ignored.
