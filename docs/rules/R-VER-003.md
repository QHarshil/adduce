# R-VER-003 — Exact revision referenced in README or manifest

**Category:** Versioning  
**Severity:** low  
**Weight:** 2

## Why it matters

A commit hash in the docs ties the written instructions to a specific code
state. A confirmed manifest claim counts as stronger evidence only when its
commit is a valid hexadecimal Git revision and matches the current checkout.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-VER-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-VER-003"]`

Suppressed findings still appear in reports, marked as ignored.
