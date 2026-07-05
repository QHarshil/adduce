# R-DEP-001 — Individual dependencies left floating

**Category:** Dependencies  
**Weight:** 3

## Why it matters

Each floating dependency is one more way the rebuilt environment differs from the original.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-001"]`

Suppressed findings still appear in reports, marked as ignored.
