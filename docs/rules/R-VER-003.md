# R-VER-003 — Exact revision referenced in README or manifest

**Category:** Versioning  
**Weight:** 2

## Why it matters

A commit hash in the docs ties the written instructions to the code state they were written for.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-VER-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-VER-003"]`

Suppressed findings still appear in reports, marked as ignored.
