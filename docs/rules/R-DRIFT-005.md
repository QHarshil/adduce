# R-DRIFT-005 — Paper's hardware/runtime claims absent from the artifact

**Category:** Paper & Artifact Consistency  
**Weight:** 2

## Why it matters

When the paper states hardware and runtime but the repository does not, the artifact cannot back the paper's compute claims.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DRIFT-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DRIFT-005"]`

Suppressed findings still appear in reports, marked as ignored.
