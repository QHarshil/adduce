# R-DATA-005 — Data-access path heuristic

**Category:** Data  
**Severity:** medium  
**Weight:** 3

## Why it matters

A documented, scripted, and integrity-checked acquisition path gives reviewers stronger static evidence that the intended data can be identified and retrieved.

Only dataset-specific signals strengthen the integrity component of this heuristic: DVC metadata, a manifest dataset checksum, or checksum verification visibly bound to a detected download. A repository-wide checksum file with no visible link to the data path does not.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DATA-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DATA-005"]`

Suppressed findings still appear in reports, marked as ignored.
