# R-RES-002 — Reported metric materially differs from the logged value

**Category:** Result Reconciliation  
**Weight:** 4

## Why it matters

When the paper's number and the closest logged value disagree beyond rounding, either the log is from a different run or the paper is stale — both need resolving before a reviewer finds it.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RES-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RES-002"]`

Suppressed findings still appear in reports, marked as ignored.
