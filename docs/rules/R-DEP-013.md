# R-DEP-013 — System/native dependencies used but undocumented

**Category:** Dependencies  
**Severity:** low  
**Weight:** 1

## Why it matters

subprocess calls to external tools (ffmpeg, git, wget) fail on machines without them; the README or Dockerfile should say what to install. Heuristic.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DEP-013`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DEP-013"]`

Suppressed findings still appear in reports, marked as ignored.
