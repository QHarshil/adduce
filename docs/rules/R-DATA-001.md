# R-DATA-001 — Data-source documentation signals

**Category:** Data  
**Severity:** medium  
**Weight:** 4

## Why it matters

Documenting each dataset's origin helps reviewers assess whether the inputs can be obtained and interpreted.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-001"]`

Suppressed findings still appear in reports, marked as ignored.
