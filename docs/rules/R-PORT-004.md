# R-PORT-004 — No hardcoded secrets or API keys

**Category:** Portability  
**Severity:** high  
**Weight:** 3

## Why it matters

A potential credential match requires prompt validation because secret
detection is heuristic. A confirmed active credential must be revoked and
removed from the published artifact and its history.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-004"]`

Suppressed findings still appear in reports, marked as ignored.
