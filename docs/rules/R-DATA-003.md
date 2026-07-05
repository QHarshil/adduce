# R-DATA-003 — Data integrity verifiable (checksums)

**Category:** Data  
**Weight:** 3

## Why it matters

Datasets silently change upstream. A checksum turns 'we used the same data' from an assumption into a check.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-003"]`

Suppressed findings still appear in reports, marked as ignored.
