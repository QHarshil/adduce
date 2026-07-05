# R-PREC-002 — Mixed precision (AMP/autocast) undocumented

**Category:** Numerical Precision & Hardware  
**Severity:** medium  
**Weight:** 3

## Why it matters

AMP changes numerics run-to-run and across GPU generations; the policy must be stated.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-002"]`

Suppressed findings still appear in reports, marked as ignored.
