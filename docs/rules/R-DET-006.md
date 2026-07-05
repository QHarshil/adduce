# R-DET-006 — random_state set on scikit-learn estimators and splitters

**Category:** Determinism & Model  
**Severity:** medium  
**Weight:** 4

## Why it matters

sklearn estimators and splitters with stochastic behaviour default to fresh entropy; results differ across runs unless random_state is fixed.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-006`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-006"]`

Suppressed findings still appear in reports, marked as ignored.
