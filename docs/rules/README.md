# Rule reference

| ID | Category | Severity | Weight | Title |
|---|---|---|---:|---|
| [R-EXEC-001](R-EXEC-001.md) | Code & Execution | high | 5 | Discoverable entrypoint |
| [R-EXEC-002](R-EXEC-002.md) | Code & Execution | medium | 4 | One-command execution path |
| [R-EXEC-003](R-EXEC-003.md) | Code & Execution | medium | 3 | Exact reproduce command recorded |
| [R-ENV-001](R-ENV-001.md) | Environment & Tooling | high | 5 | Dependencies declared and pinned |
| [R-ENV-002](R-ENV-002.md) | Environment & Tooling | medium | 3 | Lockfile capturing the transitive environment |
| [R-ENV-003](R-ENV-003.md) | Environment & Tooling | medium | 4 | Container or reproducible environment definition |
| [R-ENV-004](R-ENV-004.md) | Environment & Tooling | medium | 3 | Python version specified |
| [R-ENV-005](R-ENV-005.md) | Environment & Tooling | medium | 3 | System toolchain (CUDA, native libraries) captured or documented |
| [R-DEP-001](R-DEP-001.md) | Dependencies | medium | 3 | Individual dependencies left floating |
| [R-DEP-002](R-DEP-002.md) | Dependencies | low | 2 | Broad version ranges on result-affecting libraries |
| [R-DEP-010](R-DEP-010.md) | Dependencies | medium | 4 | Imported but undeclared (ghost) dependencies |
| [R-DEP-011](R-DEP-011.md) | Dependencies | low | 1 | Declared but apparently unused dependencies |
| [R-DEP-012](R-DEP-012.md) | Dependencies | low | 2 | Notebook imports missing from the dependency manifest |
| [R-DEP-013](R-DEP-013.md) | Dependencies | low | 1 | System/native dependencies used but undocumented |
| [R-DATA-001](R-DATA-001.md) | Data | medium | 4 | Data availability statement / provenance |
| [R-DATA-002](R-DATA-002.md) | Data | medium | 4 | Scripted or documented data-acquisition path |
| [R-DATA-003](R-DATA-003.md) | Data | medium | 3 | Data integrity verifiable (checksums) |
| [R-DATA-004](R-DATA-004.md) | Data | medium | 4 | Large binaries not committed raw into git |
| [R-DATA-005](R-DATA-005.md) | Data | medium | 3 | Data-access friction grade |
| [R-DATA-006](R-DATA-006.md) | Data | low | 1 | Raw vs processed data distinguished |
| [R-DOC-001](R-DOC-001.md) | Documentation | high | 5 | README covers install, usage, and hardware/runtime |
| [R-DOC-002](R-DOC-002.md) | Documentation | medium | 4 | Hyperparameters recorded somewhere recoverable |
| [R-DOC-003](R-DOC-003.md) | Documentation | medium | 4 | Expected outputs and results stated |
| [R-DET-001](R-DET-001.md) | Determinism & Model | high | 8 | Random seeds set across all RNG sources |
| [R-DET-002](R-DET-002.md) | Determinism & Model | medium | 4 | cuDNN determinism flags set |
| [R-DET-003](R-DET-003.md) | Determinism & Model | low | 2 | Strict determinism controls (deterministic algorithms, hash seed, CUBLAS workspace) |
| [R-DET-004](R-DET-004.md) | Determinism & Model | medium | 4 | Shuffling DataLoaders use a seeded generator |
| [R-DET-005](R-DET-005.md) | Determinism & Model | medium | 3 | Multi-worker DataLoaders reseed worker RNGs |
| [R-DET-006](R-DET-006.md) | Determinism & Model | medium | 4 | random_state set on scikit-learn estimators and splitters |
| [R-PREC-001](R-PREC-001.md) | Numerical Precision & Hardware | medium | 3 | TF32 matmul enabled but undocumented |
| [R-PREC-002](R-PREC-002.md) | Numerical Precision & Hardware | medium | 3 | Mixed precision (AMP/autocast) undocumented |
| [R-PREC-003](R-PREC-003.md) | Numerical Precision & Hardware | medium | 3 | FP16/BF16 computation without documented hardware |
| [R-PREC-004](R-PREC-004.md) | Numerical Precision & Hardware | low | 2 | set_float32_matmul_precision used but undocumented |
| [R-PREC-005](R-PREC-005.md) | Numerical Precision & Hardware | low | 2 | GPU code without documented hardware |
| [R-DRIFT-001](R-DRIFT-001.md) | Paper & Artifact Consistency | high | 5 | Paper hyperparameter differs from the authoritative code value |
| [R-DRIFT-002](R-DRIFT-002.md) | Paper & Artifact Consistency | low | 2 | Multiple candidate configs; cannot resolve which backs the paper |
| [R-DRIFT-003](R-DRIFT-003.md) | Paper & Artifact Consistency | medium | 3 | Hyperparameter reported in the paper not found in code |
| [R-DRIFT-004](R-DRIFT-004.md) | Paper & Artifact Consistency | medium | 3 | Dataset named in the paper not found in code or configs |
| [R-DRIFT-005](R-DRIFT-005.md) | Paper & Artifact Consistency | low | 2 | Paper's hardware/runtime claims absent from the artifact |
| [R-DRIFT-006](R-DRIFT-006.md) | Paper & Artifact Consistency | low | 1 | Ablations mentioned without matching configs or commands |
| [R-RES-001](R-RES-001.md) | Result Reconciliation | low | 1 | Reported metrics differ from logs only at rounding level |
| [R-RES-002](R-RES-002.md) | Result Reconciliation | medium | 4 | Reported metric materially differs from the logged value |
| [R-RES-003](R-RES-003.md) | Result Reconciliation | medium | 3 | Single-run results without variance reporting |
| [R-RES-004](R-RES-004.md) | Result Reconciliation | medium | 3 | Reported metric has no corresponding logged result |
| [R-RUN-001](R-RUN-001.md) | Run Traceability | medium | 4 | Reported results have recoverable run commands |
| [R-RUN-002](R-RUN-002.md) | Run Traceability | medium | 3 | Materialised run config disagrees with checked-in configs |
| [R-RUN-003](R-RUN-003.md) | Run Traceability | low | 2 | Batch-script resource requests undocumented for readers |
| [R-CKPT-001](R-CKPT-001.md) | Checkpoint & Experiment State | medium | 3 | Checkpoints include optimizer state |
| [R-CKPT-002](R-CKPT-002.md) | Checkpoint & Experiment State | low | 2 | Checkpoints include scheduler state |
| [R-CKPT-003](R-CKPT-003.md) | Checkpoint & Experiment State | low | 2 | Checkpoints record epoch/step |
| [R-CKPT-004](R-CKPT-004.md) | Checkpoint & Experiment State | low | 2 | Checkpoints capture RNG state |
| [R-CKPT-005](R-CKPT-005.md) | Checkpoint & Experiment State | low | 2 | Checkpoints record config/commit provenance |
| [R-NB-001](R-NB-001.md) | Notebooks | medium | 3 | Notebooks executed in linear order |
| [R-NB-002](R-NB-002.md) | Notebooks | low | 2 | Committed outputs likely stale relative to the code |
| [R-NB-003](R-NB-003.md) | Notebooks | low | 2 | Hidden-state risk (gaps in execution counts) |
| [R-NB-004](R-NB-004.md) | Notebooks | low | 2 | No !pip install inside notebook cells |
| [R-NB-005](R-NB-005.md) | Notebooks | low | 2 | No absolute/local paths in notebook cells |
| [R-NB-006](R-NB-006.md) | Notebooks | low | 2 | Seeding precedes randomness in notebooks |
| [R-NB-007](R-NB-007.md) | Notebooks | low | 1 | Notebook kernel/environment metadata present |
| [R-NB-008](R-NB-008.md) | Notebooks | low | 2 | Result-bearing notebooks have a script equivalent |
| [R-PORT-001](R-PORT-001.md) | Portability | medium | 3 | No local absolute paths |
| [R-PORT-002](R-PORT-002.md) | Portability | low | 1 | No hardcoded localhost endpoints |
| [R-PORT-003](R-PORT-003.md) | Portability | medium | 3 | No private buckets or drive links as data sources |
| [R-PORT-004](R-PORT-004.md) | Portability | high | 3 | No hardcoded secrets or API keys |
| [R-REMOTE-001](R-REMOTE-001.md) | Remote Artifacts & Rot | medium | 4 | Hugging Face references carry a revision pin |
| [R-REMOTE-002](R-REMOTE-002.md) | Remote Artifacts & Rot | low | 2 | Revision pins are commit SHAs, not branches or tags |
| [R-REMOTE-003](R-REMOTE-003.md) | Remote Artifacts & Rot | low | 2 | torch.hub.load pinned to a commit |
| [R-REMOTE-004](R-REMOTE-004.md) | Remote Artifacts & Rot | medium | 3 | Raw URL / drive / bucket downloads carry integrity checks |
| [R-REMOTE-005](R-REMOTE-005.md) | Remote Artifacts & Rot | low | 1 | Online resolution of remote references (opt-in) |
| [R-VER-001](R-VER-001.md) | Versioning | medium | 3 | Under version control |
| [R-VER-002](R-VER-002.md) | Versioning | low | 2 | Tagged release marking the reported state |
| [R-VER-003](R-VER-003.md) | Versioning | low | 2 | Exact revision referenced in README or manifest |
| [R-LIC-001](R-LIC-001.md) | Access & Legal | medium | 3 | License file present |
| [R-LIC-002](R-LIC-002.md) | Access & Legal | low | 2 | Citation metadata provided |
| [R-LIC-003](R-LIC-003.md) | Access & Legal | low | 2 | Third-party asset licenses stated |
| [R-ARC-001](R-ARC-001.md) | Archival Readiness | medium | 3 | Archival identifier (DOI / SWHID) |
| [R-ARC-002](R-ARC-002.md) | Archival Readiness | low | 1 | Repository archivable as-is |
| [R-ARC-003](R-ARC-003.md) | Archival Readiness | low | 1 | Machine-readable archival metadata (.zenodo.json / codemeta.json) |
