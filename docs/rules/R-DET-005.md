# R-DET-005 — Multi-worker DataLoaders reseed worker RNGs

**Category:** Determinism & Model  
**Severity:** medium  
**Weight:** 3

## Why it matters

DataLoader workers are separate processes: torch reseeds its own per-worker state, but numpy and random inherit unseeded state unless worker_init_fn reseeds them. This is a separate RNG source from the sampler and silently changes augmentation.

## Fix

A generated starting point is available: `adduce fix --scaffold seeds`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-005"]`

Suppressed findings still appear in reports, marked as ignored.
