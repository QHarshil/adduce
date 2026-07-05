# R-RES-003 — Single-run results without variance reporting

**Category:** Result Reconciliation  
**Severity:** medium  
**Weight:** 3

## Why it matters

Conference checklists ask explicitly for error bars; a single seed with no std/CI makes the reported difference uninterpretable.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RES-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RES-003"]`

Suppressed findings still appear in reports, marked as ignored.
