# R-EXEC-002 — One-command execution path

**Category:** Code & Execution  
**Weight:** 4

## Why it matters

A run.sh, Makefile target, or documented command removes the guesswork between cloning and reproducing.

## Fix

A generated starting point is available: `adduce fix --scaffold runner`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-EXEC-002`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-EXEC-002"]`

Suppressed findings still appear in reports, marked as ignored.
