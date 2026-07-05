# R-ARC-001 — Archival identifier (DOI / SWHID)

**Category:** Archival Readiness  
**Severity:** medium  
**Weight:** 3

## Why it matters

GitHub repositories move and disappear; an archival deposit with a persistent identifier is what 'Artifacts Available' badging requires.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ARC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ARC-001"]`

Suppressed findings still appear in reports, marked as ignored.
