# R-CKPT-005 — Checkpoints record config/commit provenance

**Category:** Checkpoint & Experiment State  
**Severity:** low  
**Weight:** 2

## Why it matters

A checkpoint that records its config, commit, and library versions can be traced back to what produced it months later; one that does not is an orphan.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-CKPT-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-CKPT-005"]`

Suppressed findings still appear in reports, marked as ignored.
