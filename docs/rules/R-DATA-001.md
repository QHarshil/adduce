# R-DATA-001 — Data availability statement / provenance

**Category:** Data  
**Weight:** 4

## Why it matters

If reviewers cannot learn where the data comes from, nothing else about the repository matters.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-001"]`

Suppressed findings still appear in reports, marked as ignored.
