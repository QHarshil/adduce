# R-PORT-004 — No hardcoded secrets or API keys

**Category:** Portability  
**Severity:** high  
**Weight:** 3

## Why it matters

A committed key is a security incident and blocks publishing the artifact at all.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-004"]`

Suppressed findings still appear in reports, marked as ignored.
