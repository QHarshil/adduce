# R-RUN-002 — Materialised run config disagrees with checked-in configs

**Category:** Run Traceability  
**Severity:** medium  
**Weight:** 3

## Why it matters

A materialised Hydra output or W&B/MLflow record can preserve the parameters
used for a run. It outranks a checked-in config only when an author-confirmed
claim links that run record; otherwise a disagreement remains visible rather
than proving which source is stale.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RUN-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RUN-002"]`

Suppressed findings still appear in reports, marked as ignored.
