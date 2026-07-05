# R-DOC-002 — Hyperparameters recorded somewhere recoverable

**Category:** Documentation  
**Weight:** 4

## Why it matters

Hyperparameters buried in code cannot be audited or swept; configs, CLI defaults, or a documented table make the exact setting recoverable.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DOC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DOC-002"]`

Suppressed findings still appear in reports, marked as ignored.
