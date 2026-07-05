# R-RES-001 — Reported metrics differ from logs only at rounding level

**Category:** Result Reconciliation  
**Weight:** 1

## Why it matters

A rounded 0.814 backed by a logged 0.8137 is healthy; surfacing it confirms the trail rather than flagging an error.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RES-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RES-001"]`

Suppressed findings still appear in reports, marked as ignored.
