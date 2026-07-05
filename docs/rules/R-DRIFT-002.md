# R-DRIFT-002 — Multiple candidate configs; cannot resolve which backs the paper

**Category:** Paper & Artifact Consistency  
**Severity:** low  
**Weight:** 2

## Why it matters

When several configs carry different values for the same hyperparameter and nothing says which run produced the paper, the reader cannot resolve it either. Ambiguity, not necessarily error.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-002"]`

Suppressed findings still appear in reports, marked as ignored.
