# R-DET-001 — Random seeds set across all RNG sources

**Category:** Determinism & Model  
**Severity:** high  
**Weight:** 8

## Why it matters

Unseeded random number generators are a leading cause of non-reproducible ML results. Each library keeps its own RNG state, so one seed call is not enough.

## Fix

A generated starting point is available: `adduce fix --scaffold seeds`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-001"]`

Suppressed findings still appear in reports, marked as ignored.
