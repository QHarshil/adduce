# R-DATA-002 — Scripted or documented data-acquisition path

**Category:** Data  
**Weight:** 4

## Why it matters

A download script (or clearly documented manual path) makes data access mechanical rather than archaeological.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-002"]`

Suppressed findings still appear in reports, marked as ignored.
