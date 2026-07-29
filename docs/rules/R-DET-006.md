# R-DET-006 — random_state set on scikit-learn estimators and splitters

**Category:** Determinism & Model  
**Severity:** medium  
**Weight:** 4

## Why it matters

When `random_state` is omitted, stochastic scikit-learn calls can depend on
mutable global RNG state and call order. An explicit value isolates each
call's randomness; omission does not prove that every run will differ.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-006`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-006"]`

Suppressed findings still appear in reports, marked as ignored.
