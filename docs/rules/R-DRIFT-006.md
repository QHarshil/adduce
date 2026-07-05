# R-DRIFT-006 — Ablations mentioned without matching configs or commands

**Category:** Paper & Artifact Consistency  
**Severity:** low  
**Weight:** 1

## Why it matters

Each ablation row is an experiment someone may try to rerun; a low-confidence hint when nothing in the repo obviously produces them.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-006`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-006"]`

Suppressed findings still appear in reports, marked as ignored.
