# R-DEP-011 — Declared but apparently unused dependencies

**Category:** Dependencies  
**Weight:** 1

## Why it matters

Unused declarations bloat the environment and slow the rebuild; heuristic, since plugins and CLI tools are used without being imported.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-011`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-011"]`

Suppressed findings still appear in reports, marked as ignored.
