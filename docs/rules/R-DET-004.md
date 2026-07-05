# R-DET-004 — Shuffling DataLoaders use a seeded generator

**Category:** Determinism & Model  
**Weight:** 4

## Why it matters

A shuffling DataLoader without an explicit generator= draws sample order from global RNG state, which changes whenever anything else consumes that state.

## Fix

A generated starting point is available: `adduce fix --scaffold seeds`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-004"]`

Suppressed findings still appear in reports, marked as ignored.
