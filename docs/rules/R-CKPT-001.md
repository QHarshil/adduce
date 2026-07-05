# R-CKPT-001 — Checkpoints include optimizer state

**Category:** Checkpoint & Experiment State  
**Severity:** medium  
**Weight:** 3

## Why it matters

Weights-only checkpoints cannot resume training identically; Adam moments restart from zero.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-CKPT-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-CKPT-001"]`

Suppressed findings still appear in reports, marked as ignored.
