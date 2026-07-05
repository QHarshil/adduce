# R-LIC-001 — License file present

**Category:** Access & Legal  
**Severity:** medium  
**Weight:** 3

## Why it matters

Without a license, reuse is legally undefined no matter how open the repository looks.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-LIC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-LIC-001"]`

Suppressed findings still appear in reports, marked as ignored.
