# R-PREC-004 — set_float32_matmul_precision used but undocumented

**Category:** Numerical Precision & Hardware  
**Weight:** 2

## Why it matters

A one-line global precision switch that silently changes every matmul in the program.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-004"]`

Suppressed findings still appear in reports, marked as ignored.
