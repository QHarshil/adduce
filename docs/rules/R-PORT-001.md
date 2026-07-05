# R-PORT-001 — No local absolute paths

**Category:** Portability  
**Severity:** medium  
**Weight:** 3

## Why it matters

Paths under /Users, /home/<name>, or C:\Users fail on every machine but one.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-001"]`

Suppressed findings still appear in reports, marked as ignored.
