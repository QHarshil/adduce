# R-PREC-002 — Mixed precision (AMP/autocast) undocumented

**Category:** Numerical Precision & Hardware  
**Severity:** medium  
**Weight:** 3

## Why it matters

AMP changes numerical precision and can interact with hardware, kernels, and
determinism settings. Recording the policy helps reviewers interpret numerical
differences; AMP alone does not prove run-to-run instability.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PREC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PREC-002"]`

Suppressed findings still appear in reports, marked as ignored.
