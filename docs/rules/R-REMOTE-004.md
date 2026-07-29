# R-REMOTE-004 — Raw URL / drive / bucket downloads carry integrity checks

**Category:** Remote Artifacts & Rot  
**Severity:** medium  
**Weight:** 3

## Why it matters

A mutable download can change independently of the repository. Adduce reports
checksum coverage only when a checksum-verification command is visibly bound
to that download's named output (or to its pipeline); an unrelated checksum
file does not establish coverage.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-004`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-004"]`

Suppressed findings still appear in reports and retain their observed score.
