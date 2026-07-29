# R-PORT-003 — Drive and object-storage data-source dependencies

**Category:** Portability  
**Severity:** medium  
**Weight:** 3

## Why it matters

Drive links and object-storage URIs may depend on owner-controlled permissions,
quotas, and retention. Static inspection cannot determine their current public
accessibility.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-003"]`

Suppressed findings still appear in reports, marked as ignored.
