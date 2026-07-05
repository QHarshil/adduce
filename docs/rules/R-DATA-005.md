# R-DATA-005 — Data-access friction grade

**Category:** Data  
**Severity:** medium  
**Weight:** 3

## Why it matters

Reviewers abandon artifacts whose data cannot be obtained quickly. This grades the access path from A (script + checksum) to E (no provenance).

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-005"]`

Suppressed findings still appear in reports, marked as ignored.
