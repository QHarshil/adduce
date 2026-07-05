# R-EXEC-003 — Exact reproduce command recorded

**Category:** Code & Execution  
**Severity:** medium  
**Weight:** 3

## Why it matters

Distinct from having *a* runner: the specific command that regenerates the reported results must be written down, in the README or the manifest, or reproduction starts with guessing flags.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-EXEC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-EXEC-003"]`

Suppressed findings still appear in reports, marked as ignored.
