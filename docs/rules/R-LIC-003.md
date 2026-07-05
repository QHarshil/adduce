# R-LIC-003 — Third-party asset licenses stated

**Category:** Access & Legal  
**Severity:** low  
**Weight:** 2

## Why it matters

Datasets and pretrained models come with their own terms; venues ask explicitly whether asset licenses were respected and stated.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-LIC-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-LIC-003"]`

Suppressed findings still appear in reports, marked as ignored.
