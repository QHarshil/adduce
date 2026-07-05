# R-ARC-002 — Repository archivable as-is

**Category:** Archival Readiness  
**Severity:** low  
**Weight:** 1

## Why it matters

Multi-gigabyte committed binaries blow past archive size limits and block a clean deposit.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ARC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ARC-002"]`

Suppressed findings still appear in reports, marked as ignored.
