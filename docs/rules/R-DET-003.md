# R-DET-003 — Strict determinism controls (deterministic algorithms, hash seed, CUBLAS workspace)

**Category:** Determinism & Model  
**Severity:** low  
**Weight:** 2

## Why it matters

`torch.use_deterministic_algorithms(True)` plus `PYTHONHASHSEED` and
`CUBLAS_WORKSPACE_CONFIG` set by the launcher before Python starts reduce known
sources of nondeterminism beyond ordinary RNG seeding. They do not cover every
operation or establish bit-exact reproduction across releases and platforms.
Setting `PYTHONHASHSEED` inside an already-running interpreter does not change
that interpreter's hash randomisation and is not counted as startup evidence.

## Suppressing

Inline, on the reported line: `# adduce: ignore=R-DET-003`  
Project-wide, in `adduce.toml` or `[tool.adduce]`: `ignore = ["R-DET-003"]`

Suppressed findings still appear in reports, marked as ignored.
