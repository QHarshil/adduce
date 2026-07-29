# R-VER-002 — Tag marking the scanned revision

**Category:** Versioning  
**Severity:** low  
**Weight:** 2

## Why it matters

A tag pointing at the scanned commit gives reviewers a stable local name for
that state. This static check does not establish that the tag was published,
retained remotely, or protected from later movement or deletion.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-VER-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-VER-002"]`

Suppressed findings still appear in reports, marked as ignored.
