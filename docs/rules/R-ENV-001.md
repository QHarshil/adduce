# R-ENV-001 — Dependencies declared and pinned

**Category:** Environment & Tooling  
**Weight:** 5

## Why it matters

Unpinned dependencies drift: the same install command produces a different environment a month later, and with it different numbers.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-001"]`

Suppressed findings still appear in reports, marked as ignored.
