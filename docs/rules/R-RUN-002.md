# R-RUN-002 — Materialised run config disagrees with checked-in configs

**Category:** Run Traceability  
**Severity:** medium  
**Weight:** 3

## Why it matters

The Hydra output (or W&B/MLflow record) is what actually ran. When it disagrees with the committed config, the committed config is the stale one.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RUN-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RUN-002"]`

Suppressed findings still appear in reports, marked as ignored.
