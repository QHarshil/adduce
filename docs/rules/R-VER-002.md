# R-VER-002 — Tagged release marking the reported state

**Category:** Versioning  
**Weight:** 2

## Why it matters

Tags make the exact state that produced the paper recoverable years later.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-VER-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-VER-002"]`

Suppressed findings still appear in reports, marked as ignored.
