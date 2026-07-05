# R-ARC-003 — Machine-readable archival metadata (.zenodo.json / codemeta.json)

**Category:** Archival Readiness  
**Weight:** 1

## Why it matters

Without deposit metadata, the Zenodo record inherits whatever GitHub guesses; .zenodo.json and codemeta.json make the archival record correct on first deposit.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ARC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ARC-003"]`

Suppressed findings still appear in reports, marked as ignored.
