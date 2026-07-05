# R-REMOTE-005 — Online resolution of remote references (opt-in)

**Category:** Remote Artifacts & Rot  
**Severity:** low  
**Weight:** 1

## Why it matters

With --online, adduce resolves current hub revisions and URL heads from the user's machine; failures here mean the remote is gone, gated, or private — rot has already begun.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-005"]`

Suppressed findings still appear in reports, marked as ignored.
