# R-DEP-012 — Notebook imports missing from the dependency manifest

**Category:** Dependencies  
**Severity:** low  
**Weight:** 2

## Why it matters

Notebook-only imports can become undeclared dependencies when an interactive
environment retains packages installed with `!pip install`. Recording them in
the dependency manifest gives reviewers a repeatable installation path.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-012`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-012"]`

Suppressed findings still appear in reports, marked as ignored.
