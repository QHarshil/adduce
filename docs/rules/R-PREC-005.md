# R-PREC-005 — GPU code without documented hardware

**Category:** Numerical Precision & Hardware  
**Weight:** 2

## Why it matters

Even in plain fp32, results differ across GPU architectures (reduction orders, denormals). Any GPU-using artifact should state what it ran on.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-005"]`

Suppressed findings still appear in reports, marked as ignored.
