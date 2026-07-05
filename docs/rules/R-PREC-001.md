# R-PREC-001 — TF32 matmul enabled but undocumented

**Category:** Numerical Precision & Hardware  
**Weight:** 3

## Why it matters

TF32 truncates float32 matmul mantissas on Ampere+ GPUs; results differ from true fp32 and from older hardware unless the setting is stated.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-001"]`

Suppressed findings still appear in reports, marked as ignored.
