# R-ENV-003 — Container or reproducible environment definition

**Category:** Environment & Tooling  
**Severity:** medium  
**Weight:** 4

## Why it matters

A Dockerfile or devcontainer captures the system layer (CUDA, native libraries) that Python manifests cannot express.

## Fix

A generated starting point is available: `adduce fix --scaffold docker`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-003"]`

Suppressed findings still appear in reports, marked as ignored.
