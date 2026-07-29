# R-RES-004 — Reported metric lacks a detected matching logged column

**Category:** Result Reconciliation  
**Severity:** medium  
**Weight:** 3

## Why it matters

A detected correspondence between a reported metric and stored result data gives reviewers a traceable basis for checking the claim.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-RES-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-RES-004"]`

Suppressed findings still appear in reports, marked as ignored.
