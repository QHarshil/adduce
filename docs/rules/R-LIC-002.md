# R-LIC-002 — Citation metadata provided

**Category:** Access & Legal  
**Weight:** 2

## Why it matters

CITATION.cff is machine-readable citation metadata that GitHub and Zenodo pick up automatically; a BibTeX block in the README is the manual fallback.

## Fix

A generated starting point is available: `adduce fix --scaffold citation`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-LIC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-LIC-002"]`

Suppressed findings still appear in reports, marked as ignored.
