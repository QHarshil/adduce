# R-VER-001 — Under version control

**Category:** Versioning  
**Severity:** medium  
**Weight:** 3

## Why it matters

Results belong to a commit, not a directory; without git there is no commit.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-VER-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-VER-001"]`

Suppressed findings still appear in reports, marked as ignored.
