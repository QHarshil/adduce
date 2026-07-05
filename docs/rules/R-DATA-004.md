# R-DATA-004 — Large binaries not committed raw into git

**Category:** Data  
**Severity:** medium  
**Weight:** 4

## Why it matters

Weights or datasets committed straight into git usually mean the 'reproduction' ships outputs rather than reruns them, and they bloat every clone.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-004"]`

Suppressed findings still appear in reports, marked as ignored.
