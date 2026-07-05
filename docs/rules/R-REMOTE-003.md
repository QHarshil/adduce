# R-REMOTE-003 — torch.hub.load pinned to a commit

**Category:** Remote Artifacts & Rot  
**Weight:** 2

## Why it matters

torch.hub.load('owner/repo') tracks the default branch of a GitHub repo — maximal rot exposure.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-003"]`

Suppressed findings still appear in reports, marked as ignored.
