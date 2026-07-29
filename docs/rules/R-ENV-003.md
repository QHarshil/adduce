# R-ENV-003 — Container or reproducible environment definition

**Category:** Environment & Tooling  
**Severity:** medium  
**Weight:** 4

## Why it matters

A complete Dockerfile or devcontainer can record system dependencies such as
CUDA and native libraries that Python manifests cannot express. This rule
detects the definition's presence; it does not validate that the definition is
complete or builds successfully.

## Fix

A generated starting point is available: `adduce fix --scaffold docker`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-003"]`

Suppressed findings still appear in reports, marked as ignored.
