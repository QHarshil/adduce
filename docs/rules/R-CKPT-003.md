# R-CKPT-003 — Checkpoints record epoch/step

**Category:** Checkpoint & Experiment State  
**Weight:** 2

## Why it matters

Without the training position, resuming re-runs or skips data and breaks comparisons.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-CKPT-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-CKPT-003"]`

Suppressed findings still appear in reports, marked as ignored.
