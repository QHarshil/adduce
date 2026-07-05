# R-DRIFT-003 — Hyperparameter reported in the paper not found in code

**Category:** Paper & Artifact Consistency  
**Weight:** 3

## Why it matters

A stated setting with no code counterpart cannot be verified or reproduced.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-003"]`

Suppressed findings still appear in reports, marked as ignored.
