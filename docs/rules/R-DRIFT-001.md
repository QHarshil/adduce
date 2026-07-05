# R-DRIFT-001 — Paper hyperparameter differs from the authoritative code value

**Category:** Paper & Artifact Consistency  
**Severity:** high  
**Weight:** 5

## Why it matters

Configs get tuned after the paper freezes; a stated learning rate that no config contains is the classic camera-ready drift.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-001"]`

Suppressed findings still appear in reports, marked as ignored.
