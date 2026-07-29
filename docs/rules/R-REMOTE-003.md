# R-REMOTE-003 — torch.hub.load pinned to a commit

**Category:** Remote Artifacts & Rot  
**Severity:** low  
**Weight:** 2

## Why it matters

`torch.hub.load('owner/repo')` follows a repository branch by default, so the
referenced code may change between runs. A commit identifies content but does
not by itself establish long-term availability.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-003"]`

Suppressed findings still appear in reports, marked as ignored.
