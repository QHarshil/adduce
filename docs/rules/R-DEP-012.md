# R-DEP-012 — Notebook imports missing from the dependency manifest

**Category:** Dependencies  
**Severity:** low  
**Weight:** 2

## Why it matters

Notebook-only imports are the most common ghost dependencies: installed once with !pip install, never declared, gone on the reviewer's machine.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-012`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-012"]`

Suppressed findings still appear in reports, marked as ignored.
