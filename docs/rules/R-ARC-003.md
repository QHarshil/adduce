# R-ARC-003 — Machine-readable archival metadata (.zenodo.json / codemeta.json)

**Category:** Archival Readiness  
**Severity:** low  
**Weight:** 1

## Why it matters

Without reviewed deposit metadata, an archive may infer incomplete fields from
the repository host. `.zenodo.json` and `codemeta.json` provide an explicit
draft for author review; their presence does not establish that the metadata
is correct.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ARC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ARC-003"]`

Suppressed findings still appear in reports, marked as ignored.
