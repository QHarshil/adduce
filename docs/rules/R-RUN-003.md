# R-RUN-003 — Batch-script resource requests undocumented for readers

**Category:** Run Traceability  
**Severity:** low  
**Weight:** 2

## Why it matters

SLURM directives encode the real hardware requirements (GPUs, memory, walltime); when only the batch script knows them, README readers plan with wrong expectations.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RUN-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RUN-003"]`

Suppressed findings still appear in reports, marked as ignored.
