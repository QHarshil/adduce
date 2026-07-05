# adduce

**A local research-artifact auditor.**

`adduce` checks whether a paper's claims, code, configs, data, dependencies, remote models, precision settings, and generated results still agree with each other before submission — and produces the artifacts reviewers and conferences ask for: filled NeurIPS/ACL checklists, an ACM Artifact Appendix, archival metadata (RO-Crate, Croissant, CodeMeta, Zenodo), and a claim-by-claim evidence trail.

```
pipx install adduce        # or: pip install adduce / uvx adduce
adduce check .
```

The north-star question: *for every number in the paper, can I point to the artifact that produced it, and will that artifact still produce it elsewhere?*

> `adduce` is offline by default. It never sends repository contents anywhere. Online checks are opt-in (`--online` or the `pin-remotes`/`archive-plan` commands) and only resolve public remote metadata such as Hugging Face model and dataset revisions, GitHub release SHAs, and URL headers. Resolved values are cached in `.adduce/cache` and written to the manifest only when requested. No server is operated by the project; all requests originate from the user's machine.

## What it reports

```
╭─ adduce  ·  cinematch  ·  commit 3f9a1c2 ─────────────────────────────────────╮
│ Reproducibility  78/100   Silver   ·   profile: default                       │
╰────────────────────────────────────────────────────────────────────────────────╯
Reviewer time to first result: 45–90 min (Risky)
  - no one-command reproduction path
  - expected runtime not documented

  Category                 Score   Notes
  Code & Execution         10/12   commands documented; no run script
  Determinism & Model       8/12   seeds set; DataLoader workers unseeded
  Paper & Artifact Consist. 4/8    learning_rate: paper says 0.0001, configs/main.yaml has 0.001
  ...

Claim trails (manifest)
  Table 2  ·  "LambdaMART improves NDCG@10 to 0.814"
    metric      results/lambdamart_eval.csv  (found: 0.8127)   ~ rounding vs paper (0.814) ✓
    command     make eval-lambdamart
    config      configs/lambdamart.yaml ✓
    data        data/splits/ml-25m/test.json ✓
    env         uv.lock + Dockerfile ✓
    seeds       42, 43, 44
    status      PARTIAL
```

Every finding carries a status (`pass` / `partial` / `fail` / `not-applicable` / `unknown`), a confidence, file:line locations, and a concrete remediation. `partial` is the most common and most useful state.

## The three layers, and which one this is

The reproducibility problem has three layers. **Sharing** (findable, licensed, citable) is owned by FAIR tools like `howfairis`. **Packaging** (capture and replay execution) is owned by ReproZip, DataLad, and repo2docker. **Traceability** — does each reported claim map to the exact code, config, data, seed, environment, command, and logged result that produced it — is the layer reviewers actually probe, and the layer `adduce` owns, folding the other two in as inputs.

## The Reproducibility Manifest

`.adduce/manifest.yaml` is the machine-readable source of truth. `adduce manifest` drafts it from detected evidence — claims extracted from the paper, datasets from loaders, unpinned remotes, the environment — and the author confirms it. Every other command consumes it: manifest-declared links are authoritative, inferred links carry confidence.

```yaml
schema: adduce/1
claims:
  - id: C1
    text: "LambdaMART achieves NDCG@10 of 0.814"
    where: "Table 2"
    metric: "ndcg@10"
    value: 0.814
    seeds: [42, 43, 44]
    produced_by:
      command: "make eval-lambdamart"
      config: configs/lambdamart.yaml
      log: results/lambdamart_eval.csv
smoke:
  command: "python train.py --config configs/smoke.yaml"
  max_runtime_minutes: 10
  expected_outputs: ["results/smoke_metrics.json"]
```

The `smoke` target is the biggest usability lever an artifact can have: it lets a reviewer verify the pipeline's shape in minutes instead of "download 200 GB and train for three days."

## What it checks

78 rules across 17 categories:

| Category | Prefix | Examples |
|---|---|---|
| Code & Execution | `R-EXEC` | entrypoint, one-command runner, exact reproduce command |
| Environment & Tooling | `R-ENV` | pinning posture, lockfile, container, Python version, CUDA capture |
| Dependencies | `R-DEP` | ghost imports, unused declarations, notebook-only imports, system tools |
| Data | `R-DATA` | provenance, download path, checksums, LFS, access-friction grade A–E |
| Documentation | `R-DOC` | README sections, hyperparameters recorded, expected results |
| Determinism & Model | `R-DET` | layered seeds, cuDNN flags, strict mode, both DataLoader RNG sources, `random_state` |
| Numerical Precision & Hardware | `R-PREC` | undocumented TF32/AMP/bf16, hardware baseline (warnings, never fails) |
| Paper & Artifact Consistency | `R-DRIFT` | paper hyperparameter vs authoritative config, dataset drift, ablation traces |
| Result Reconciliation | `R-RES` | reported vs logged metrics, rounding vs material gaps, single-run detection |
| Run Traceability | `R-RUN` | per-claim commands, materialised Hydra configs vs committed ones, SLURM requests |
| Checkpoint & Experiment State | `R-CKPT` | optimizer/scheduler/RNG state, epoch, config/commit provenance in checkpoints |
| Notebooks | `R-NB` | execution order, hidden state, `!pip install` cells, seed-before-draw, script twins |
| Portability | `R-PORT` | absolute paths, localhost, drive-link data sources, committed secrets |
| Remote Artifacts & Rot | `R-REMOTE` | unpinned `from_pretrained`, mutable revisions, `torch.hub`, checksum-less downloads |
| Versioning | `R-VER` | git, tags, commit referenced in docs |
| Access & Legal | `R-LIC` | LICENSE, CITATION.cff, third-party asset licenses |
| Archival Readiness | `R-ARC` | DOI/SWHID, archivable size, `.zenodo.json`/`codemeta.json` |

Drift resolution uses an explicit authority ranking: a materialised run config (Hydra output, W&B, MLflow) outranks a checked-in config, which outranks an argparse/dataclass default — a default alone is weak evidence of what actually ran. Floats compare with rounding-awareness (a paper's 0.814 matches a logged 0.8137); nothing ever auto-edits the `.tex`.

Call resolution goes through an import-alias map (`import torch as th` is handled) plus one hop of wrapper resolution: a project-local `set_seed()` that calls the primitives counts. Python's dynamism (`getattr`, dynamic import) cannot be resolved statically — which is exactly why findings carry a confidence, never a verdict.

## Commands

```bash
adduce check .                       # everything offline: report, claim trails, reviewer time
adduce check --mode reviewer         # skeptical framing: what could not be verified
adduce check --mode ae-chair         # badge eligibility, blocking issues, burden headline
adduce check -f json|sarif|markdown|badge|latex -o out
adduce drift                         # paper ↔ code/config consistency + result reconciliation
adduce precision                     # TF32/AMP/low-precision audit
adduce deps                          # ghost/unused/notebook dependency analysis
adduce manifest                      # scaffold/refresh .adduce/manifest.yaml
adduce checklist --profile neurips   # filled reproducibility checklist (also: acl)
adduce appendix                      # ACM Artifact Appendix draft
adduce export ro-crate|croissant|codemeta|zenodo|checksums|software-heritage|all
adduce badge --svg                   # committed-in-repo badge; no hosted endpoint
adduce diff main...HEAD              # artifact regression: code changed, docs/manifest did not?
adduce archive-plan                  # exact steps to a Zenodo DOI / Software Heritage SWHID
adduce baseline                      # snapshot for the CI ratchet
adduce rules · adduce explain R-DET-001
adduce fix --scaffold seeds|docker|citation|runner|readme

# opt-in, clearly fenced:
adduce pin-remotes --diff            # resolve current HF/GitHub SHAs (online), show pin diffs
adduce reproduce --yes               # run the smoke target twice, assert the runs agree (executes repo code)
```

`adduce reproduce` is the empirical layer: two runs with a pinned seed, fingerprinted (output hashes, stdout metrics), compared. It executes repository code, so it demands `--yes`, is designed to run inside the repo's own container or CI, and is never invoked by `check`. A first-use ordering diagnostic (`python -m adduce.dynamic.import_hook train.py`) reports whether seeding precedes the first RNG draw.

`adduce pin-remotes` resolves current revisions and drafts `revision="<sha>"` edits as diffs (libcst codemods, applied only with `--write`). Pinning to the *current* SHA is a forward guarantee — it does not recover the version historically used, and the output says so.

## Reviewer time to first result

The score reframed into the currency a PI feels: `< 10 min` Excellent · `10–30` Good · `30–90` Risky · `90+` High reviewer burden — with the factors named (no one-command path, manual data fetch, no smoke target, undocumented runtime), so the author knows exactly what buys time back.

## Scoring, profiles, suppression

Scoring is category-weighted and explainable — each category reports earned/possible with the findings that moved it; inapplicable categories drop out and the rest renormalise, so a scikit-learn repository is never scored against CUDA flags. Profiles: `default`, `neurips`, `iclr`, `acl`, `acm`, `strict`, or your own TOML.

```python
loader = DataLoader(ds, shuffle=True)  # adduce: ignore=R-DET-004
```

```toml
[tool.adduce]           # or adduce.toml
profile = "neurips"
ignore = ["R-ARC-001"]
exclude = ["third_party"]
```

Suppressed findings still appear, marked as ignored.

## Continuous integration

The default run is diagnostic: `adduce check` exits 0 regardless of score. Gate with `--fail-under N`, or adopt incrementally with `adduce baseline` + `--fail-on-regression`, which fails only when a rule gets *worse* than the committed `.adduce/baseline.json` — new rules are never regressions, so upgrading the tool never punishes you.

```yaml
# .github/workflows/reproducibility.yml
name: reproducibility
on: [pull_request]
jobs:
  adduce:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: QHarshil/adduce@v1
        with:
          profile: neurips
          report-file: adduce-report.md   # lands in the job summary
          sarif-file: adduce.sarif
      - uses: github/codeql-action/upload-sarif@v3   # code-scanning alerts on public repos
        with:
          sarif_file: adduce.sarif
```

A pre-commit hook ships as well (`id: adduce`).

## Extending adduce

Rules and reporters are discovered through entry points — the flake8/pytest pattern. A lab rule pack is an ordinary package:

```python
# my_lab_rules.py
from adduce.rules import Category, Rule, Status

class SlurmScriptRule(Rule):
    id = "R-LAB-001"
    category = Category.CODE_EXECUTION
    title = "SLURM submission script present"
    rationale = "Our cluster reproductions start from a submit script."
    weight = 3

    def evaluate(self, ev):
        scripts = ev.repo.find("slurm/*.sh") + ev.repo.find("*.sbatch")
        if scripts:
            return self.finding(Status.PASS, 0.9, f"Found {scripts[0].path}.")
        return self.finding(Status.FAIL, 0.8, "No SLURM script found.",
                            remediation="Add slurm/submit.sh for the main experiment.")

RULES = [SlurmScriptRule]
```

```toml
[project.entry-points."adduce.rules"]
my_lab = "my_lab_rules"
# reporters: [project.entry-points."adduce.reporters"]  name = "module:render"
```

Installing the pack is all it takes.

## Optional LLM layer

Strictly separated from checks and scoring, which stay deterministic and offline. With a configured provider (`ADDUCE_LLM_PROVIDER=openai|anthropic|ollama`, bring your own key or a local model), `adduce checklist --llm` drafts the free-text justification prose from the deterministic evidence. Without one, everything works identically. adduce ships no key and never calls a paid API on your behalf.

## Honest limits

- **Signals, never certification.** adduce reports what it detected and what it could not; it never says "your code is reproducible", and it never assesses execution-based badges (Results Reproduced/Replicated).
- **Static resolution has a ceiling.** Alias plus one-hop wrapper resolution covers the common shapes of real ML code; Python's dynamism is unresolvable and reported as confidence, not verdicts, with `adduce reproduce` as the escape hatch.
- **The probabilistic rules are diagnostic.** LaTeX numeric extraction, result reconciliation, notebook staleness, and ablation matching will sometimes miss or over-flag; they carry confidence and stay off the blocking path by default.
- **Remote pinning is a forward guarantee**, not recovery of the version historically used.
- **CUDA/cuDNN versions are rarely in source.** adduce checks whether anything *captures* them (container, conda env, manifest), not that it can read them from code.
- **Not a data-leakage detector.** Train/test contamination is undetectable statically and adduce claims nothing about it.
- **No hosted backend, ever.** The design is deliberately serverless so it stays free.

## Development

```bash
git clone https://github.com/QHarshil/adduce
cd adduce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src tests
```

Validation against real repositories is a standing quality gate — see [corpus/README.md](corpus/README.md) for the protocol and what may honestly be claimed from it. Contributions are welcome, especially false-positive reports: a check that cries wolf is a bug. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
