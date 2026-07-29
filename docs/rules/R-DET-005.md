# R-DET-005 — Multi-worker DataLoaders reseed worker RNGs

**Category:** Determinism & Model  
**Severity:** medium  
**Weight:** 3

## Why it matters

DataLoader workers receive distinct PyTorch seeds, while other libraries and
version-specific worker behavior can require explicit initialization. A
`worker_init_fn` makes third-party RNG policy visible and derives it from the
worker seed.

## Fix

A generated starting point is available: `adduce fix --scaffold seeds`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-005"]`

Suppressed findings still appear in reports, marked as ignored.
