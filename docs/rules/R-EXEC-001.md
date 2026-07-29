# R-EXEC-001 — Discoverable entrypoint

**Category:** Code & Execution  
**Severity:** high  
**Weight:** 5

## Why it matters

When the primary entrypoint is neither documented nor conventionally named, a
reviewer must infer which command is intended to produce the reported results.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-EXEC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-EXEC-001"]`

Suppressed findings still appear in reports, marked as ignored.
