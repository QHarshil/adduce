# R-REMOTE-004 — Raw URL / drive / bucket downloads carry integrity checks

**Category:** Remote Artifacts & Rot  
**Severity:** medium  
**Weight:** 3

## Why it matters

A wget with no checksum fetches whatever the server serves that day; with a checksum it fetches the artifact or fails loudly.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-004"]`

Suppressed findings still appear in reports, marked as ignored.
