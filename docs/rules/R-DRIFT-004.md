# R-DRIFT-004 — Dataset named in the paper not found in code or configs

**Category:** Paper & Artifact Consistency  
**Severity:** medium  
**Weight:** 3

## Why it matters

The dataset the paper names and the dataset the code loads must be the same thing.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-004"]`

Suppressed findings still appear in reports, marked as ignored.
