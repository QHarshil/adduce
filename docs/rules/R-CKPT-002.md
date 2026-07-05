# R-CKPT-002 — Checkpoints include scheduler state

**Category:** Checkpoint & Experiment State  
**Severity:** low  
**Weight:** 2

## Why it matters

A resumed run with a reset LR schedule follows a different training trajectory.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-CKPT-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-CKPT-002"]`

Suppressed findings still appear in reports, marked as ignored.
