# R-DET-003 — Strict determinism controls (deterministic algorithms, hash seed, CUBLAS workspace)

**Category:** Determinism & Model  
**Weight:** 2

## Why it matters

torch.use_deterministic_algorithms(True), PYTHONHASHSEED, and CUBLAS_WORKSPACE_CONFIG close the remaining nondeterminism that seeds and cuDNN flags do not cover.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-003"]`

Suppressed findings still appear in reports, marked as ignored.
