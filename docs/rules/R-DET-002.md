# R-DET-002 — cuDNN determinism flags set

**Category:** Determinism & Model  
**Severity:** medium  
**Weight:** 4

## Why it matters

cuDNN selects convolution algorithms at runtime; without deterministic=True and benchmark=False, the same seeded run can produce different numbers on the same GPU.

## Fix

A generated starting point is available: `adduce fix --scaffold seeds`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-002"]`

Suppressed findings still appear in reports, marked as ignored.
