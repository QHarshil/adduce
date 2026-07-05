# R-DATA-006 — Raw vs processed data distinguished

**Category:** Data  
**Severity:** low  
**Weight:** 1

## Why it matters

When raw inputs and derived artifacts share a directory, nobody can tell what is input and what is output — a standard data-engineering convention solves it.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-006`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-006"]`

Suppressed findings still appear in reports, marked as ignored.
