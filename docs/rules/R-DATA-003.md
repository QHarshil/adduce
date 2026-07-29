# R-DATA-003 — Dataset-integrity evidence

**Category:** Data  
**Severity:** medium  
**Weight:** 3

## Why it matters

Dataset-specific checksums and content-addressed metadata make upstream changes detectable. Static evidence shows that a verification path exists, not that it ran.

Adduce treats checksum verification visibly bound to a detected download, or DVC metadata, as strong static evidence. A manifest-declared checksum or an unlinked checksum file is partial evidence because the scan cannot establish that the acquisition path uses it.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-003"]`

Suppressed findings still appear in reports, marked as ignored.
