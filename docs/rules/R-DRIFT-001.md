# R-DRIFT-001 — Paper hyperparameter differs from the highest-ranked code value

**Category:** Paper & Artifact Consistency  
**Severity:** high  
**Weight:** 5

## Why it matters

Comparing paper statements with author-linked run configs, committed configs, and code defaults can identify values that need manual reconciliation.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-001"]`

Suppressed findings still appear in reports, marked as ignored.
