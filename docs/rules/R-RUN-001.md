# R-RUN-001 — Reported results have recoverable run commands

**Category:** Run Traceability  
**Severity:** medium  
**Weight:** 4

## Why it matters

For each claim, some command must recoverably produce it — from the manifest, a script, or a documented invocation. R-EXEC-003 asks whether any command is documented; this asks per claim.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RUN-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RUN-001"]`

Suppressed findings still appear in reports, marked as ignored.
