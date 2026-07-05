# R-PORT-003 — No private buckets or drive links as data sources

**Category:** Portability  
**Severity:** medium  
**Weight:** 3

## Why it matters

Google Drive links and s3:///gs:// buckets rot, throttle, and are frequently permissioned; they are the least durable data path an artifact can have.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-PORT-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-PORT-003"]`

Suppressed findings still appear in reports, marked as ignored.
