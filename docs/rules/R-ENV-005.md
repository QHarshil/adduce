# R-ENV-005 — System toolchain (CUDA, native libraries) captured or documented

**Category:** Environment & Tooling  
**Severity:** medium  
**Weight:** 3

## Why it matters

CUDA and cuDNN versions are rarely visible in source; the honest check is whether anything records them — a container base image, a conda env, or the manifest/README.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-ENV-005`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-ENV-005"]`

Suppressed findings still appear in reports, marked as ignored.
