# R-PREC-003 — FP16/BF16 computation without documented hardware

**Category:** Numerical Precision & Hardware  
**Severity:** medium  
**Weight:** 3

## Why it matters

BF16 exists only on specific architectures and FP16 behaviour differs across them; low-precision results are not interpretable without the GPU/TPU stated.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-003"]`

Suppressed findings still appear in reports, marked as ignored.
