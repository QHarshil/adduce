# R-REMOTE-002 — Revision pins are commit SHAs, not branches or tags

**Category:** Remote Artifacts & Rot  
**Weight:** 2

## Why it matters

A branch or tag revision moves; only a commit SHA is immutable on the hub.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-002"]`

Suppressed findings still appear in reports, marked as ignored.
