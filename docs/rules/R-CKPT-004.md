# R-CKPT-004 — Checkpoints capture RNG state

**Category:** Checkpoint & Experiment State  
**Severity:** low  
**Weight:** 2

## Why it matters

Identical resumption needs the RNG states (torch, cuda, numpy, random) saved and restored.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-CKPT-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-CKPT-004"]`

Suppressed findings still appear in reports, marked as ignored.
