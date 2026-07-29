# R-REMOTE-005 — Online resolution of remote references (opt-in)

**Category:** Remote Artifacts & Rot  
**Severity:** low  
**Weight:** 1

## Why it matters

With `--online`, Adduce checks supported public metadata from the user's
machine. A terminal 2xx response establishes current availability for the exact
detected identifier; redirects are independently validated. A failed request,
an unsupported 3xx response, or an identifier above the documented 8,192-byte
URL limit remains inconclusive because policy, access, and network conditions
can also prevent resolution.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-005"]`

Suppressed findings still appear in reports, marked as ignored.
