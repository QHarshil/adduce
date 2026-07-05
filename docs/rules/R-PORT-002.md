# R-PORT-002 — No hardcoded localhost endpoints

**Category:** Portability  
**Weight:** 1

## Why it matters

A hardcoded localhost:port assumes a service the reviewer's machine is not running.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-002"]`

Suppressed findings still appear in reports, marked as ignored.
