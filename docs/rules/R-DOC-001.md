# R-DOC-001 — README covers install, usage, and hardware/runtime

**Category:** Documentation  
**Weight:** 5

## Why it matters

The README is the front door; these sections are the minimum a reproducer needs.

## Fix

A generated starting point is available: `adduce fix --scaffold readme`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DOC-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DOC-001"]`

Suppressed findings still appear in reports, marked as ignored.
