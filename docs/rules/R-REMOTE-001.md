# R-REMOTE-001 — Hugging Face references carry a revision pin

**Category:** Remote Artifacts & Rot  
**Weight:** 4

## Why it matters

from_pretrained/load_dataset without revision= float on the hub's main branch; the artifact silently changes when upstream pushes.

## Fix

A generated starting point is available: `adduce pin-remotes --diff`.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-REMOTE-001`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-REMOTE-001"]`

Suppressed findings still appear in reports, marked as ignored.
