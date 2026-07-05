# R-DEP-002 — Broad version ranges on result-affecting libraries

**Category:** Dependencies  
**Weight:** 2

## Why it matters

A range like torch>=1.0 admits releases years apart; for numerics-bearing libraries the admitted spread is the reproducibility gap.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-002"]`

Suppressed findings still appear in reports, marked as ignored.
