# adduce

**A local research-artifact auditor.**

`adduce` checks whether a paper's claims, code, configs, data, dependencies, remote models, precision settings, and generated results still agree with each other before submission. It also drafts repository-observable NeurIPS/ACL checklist items, an ACM Artifact Appendix, archival metadata (RO-Crate, Croissant, CodeMeta, Zenodo), and a claim-by-claim evidence trail for author review.

```
pipx install adduce        # or: pip install adduce / uvx adduce
adduce check .
```

[PyPI `0.1.2`](https://pypi.org/project/adduce/0.1.2/) is the current release.

Adduce is beta software. Findings are static-analysis signals for review, and
scores, tiers, and reviewer-time estimates are provisional pending calibration
against manually reviewed real repositories. Generated submission material is
always a draft.

Existing installations do not update automatically. Upgrade with the command
for the installer you used:

```console
python -m pip install --upgrade adduce
pipx upgrade adduce
uv tool upgrade adduce
```

For a one-off run that explicitly selects the latest release, use
`uvx adduce@latest --version`.

The north-star question: *for every number in the paper, can I point to the artifact that produced it, and will that artifact still produce it elsewhere?*

> Built-in checks are offline by default. Public-metadata requests require the
> explicit `--online` or `pin-remotes` modes; they use a bounded public-HTTPS
> resolver, and pre-existing repository cache entries never count as network
> evidence. See the [security model](https://github.com/QHarshil/adduce/blob/main/docs/security-model.md) before using
> network, provider, plugin, or dynamic-execution features on untrusted input.

## What it reports

Trimmed output captured from running `adduce check` on [nanoGPT](https://github.com/karpathy/nanoGPT) at commit `3adf61e`:

```
╭─ adduce  ·  nanoGPT  ·  commit 3adf61e ──────────────────────────────────────╮
│ Reproducibility  54/100   Bronze   ·   profile: default                      │
╰──────────────────────────────────────────────────────────────────────────────╯
Reviewer time to first result: 23–83 min (Risky)
  - no one-command reproduction path
  - environment must be assembled by hand (no container or conda env)
  - no smoke/quick-run target for a minutes-scale sanity check

Category                        Score  Notes
Environment & Tooling            1/10  No dependency manifest found
                                       (requirements.txt, pyproject.toml, ...)
Determinism & Model              3/12  Some RNG sources are seeded, but not all:
                                       missing python (random.seed), numpy;
                                       neither cudnn.deterministic=True nor ...
Numerical Precision & Hardware   2/4   TF32 matmul precision control in use
                                       (torch.backends.cuda.matmul.allow_tf32 =
                                       True) but no precision policy documented
Checkpoint & Experiment State    2/3   No torch.save site visibly includes
                                       LR-scheduler state or epoch/step progress

Top fixes (largest score gains first)
 1. Extend the seeding helper to cover: python (random.seed), numpy.
      adduce fix --scaffold seeds
 2. Set cudnn.deterministic = True and cudnn.benchmark = False.
      adduce fix --scaffold seeds
 3. Declare dependencies, then pin them (pip-compile, uv lock, poetry lock).
 4. Add revision="<commit-sha>" to each from_pretrained call.
      adduce pin-remotes --diff
```

Location-bearing findings are anchored to source lines—the TF32 finding above points at `train.py:107`, and the unpinned hub call at `model.py:238`. When a manifest declares claims, the report adds a per-claim trail:

```
Claim trails (manifest)
  Table 2  ·  "LambdaMART improves NDCG@10 to 0.814"
    metric      results/lambdamart_eval.csv  (found: 0.8127)   ~ rounding vs paper (0.814) ✓
    command     make eval-lambdamart
    config      configs/lambdamart.yaml ✓
    seeds       42, 43, 44
    status      PARTIAL
```

Every finding carries a status (`pass` / `partial` / `fail` / `not-applicable` / `unknown`), a confidence, available file:line locations, and a concrete remediation. `partial` is used when the repository supports only part of a check.

## The three layers, and which one this is

The reproducibility problem has three layers. FAIR tools such as `howfairis` focus on **sharing** (findable, licensed, citable). ReproZip, DataLad, and repo2docker focus on **packaging** (capture and replay execution). `adduce` focuses on **traceability**: whether each reported claim maps to the code, config, data, seed, environment, command, and logged result that produced it, while using sharing and packaging signals as inputs.

## The Reproducibility Manifest

`.adduce/manifest.yaml` is the machine-readable source of truth. `adduce manifest`
scaffolds it from repository-observable evidence such as candidate result
tables, datasets from loaders, unpinned remotes, and environment files. Inferred
claim fields are draft placeholders for author confirmation, not reliable claim
discovery. New author-reviewed claims should use `status: confirmed`; generated
claims use `status: draft`. Status-less claims retain the legacy 0.1.x
non-draft behavior in claim-trail parsing for compatibility. They do not count
as author-confirmed evidence for generated checklist or appendix statements,
and the exact-revision trust check also requires explicit confirmation. Draft
and inferred links remain provisional. Refreshes are written as separate
proposal files so comments, extensions, and author content are never
overwritten.

```yaml
schema: adduce/1
claims:
  - id: C1
    status: confirmed
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

A `smoke` target can substantially reduce reviewer setup time by checking the pipeline's shape without requiring the full experiment.

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

Drift resolution uses an explicit authority ranking: a materialised run config (Hydra output, W&B, MLflow) outranks a checked-in config only when an author-confirmed claim links that run config; checked-in configs otherwise outrank argparse/dataclass defaults. Floats compare with rounding-awareness (a paper's 0.814 matches a logged 0.8137); nothing ever auto-edits the `.tex`.

Call resolution goes through an import-alias map (`import torch as th` is handled) plus one hop of wrapper resolution: a project-local `set_seed()` that calls the primitives counts. Python's dynamism (`getattr`, dynamic import) cannot be resolved statically — which is exactly why findings carry a confidence, never a verdict.

## Commands

```bash
adduce check .                       # everything offline: report, claim trails, reviewer time
adduce check --mode reviewer         # skeptical framing: what could not be verified
adduce check --mode ae-chair         # badge prerequisites, blocking issues, burden headline
adduce check -f json|sarif|markdown|badge|latex -o out
adduce check ./code --paper ../paper       # paper and code kept in separate repositories
adduce drift                         # paper ↔ code/config consistency + result reconciliation
adduce precision                     # TF32/AMP/low-precision audit
adduce deps                          # ghost/unused/notebook dependency analysis
adduce manifest                      # scaffold .adduce/manifest.yaml
adduce manifest --refresh            # write a separate refresh proposal; never overwrite author content
adduce checklist --profile neurips   # repository-evidence checklist draft (also: acl); --strict-evidence
adduce appendix                      # ACM Artifact Appendix draft; --strict-evidence
adduce package --profile neurips     # one-command submission bundle (checklist, appendix,
                                     # manifest, ledger, checksums, RO-Crate) in adduce-submission/
adduce audit-generated checklist.md  # audit a generated artifact against its evidence ledger
adduce export ro-crate|croissant|codemeta|zenodo|checksums|software-heritage|all
adduce badge --svg                   # committed-in-repo badge; no hosted endpoint
adduce diff main...HEAD              # artifact regression: code changed, docs/manifest did not?
adduce archive-plan                  # exact steps to a Zenodo DOI / Software Heritage SWHID
adduce baseline                      # snapshot for the CI ratchet
adduce rules · adduce explain R-DET-001
adduce fix --scaffold seeds|docker|citation|runner|readme

# opt-in, clearly fenced:
adduce pin-remotes --diff            # resolve current Hugging Face revisions (online), show pin diffs
adduce reproduce --yes               # run the smoke target twice, assert the runs agree (executes repo code)
```

`adduce reproduce` is the empirical layer: two runs with the same declared seed environment, fingerprinted (output hashes and stdout metrics), and compared. The target command must actually consume `ADDUCE_SEED` or apply its own equivalent seeding; Adduce does not inject framework seed calls. It executes repository code, so it demands `--yes`, is designed to run inside a disposable container or virtual machine, and is never invoked by `check`. Repository copying provides input isolation only; it does not provide process, credential, filesystem, device, resource, or network isolation. Adduce bounds captured streams and expected-output hashing, but those safeguards do not limit what the command can access or consume. The reproduction report records the selected command and parsed numeric metric names and values verbatim, although it does not retain the captured streams; do not put credentials in commands or metric names, and review the report before sharing it. The best-effort first-use diagnostic (`adduce-rng-audit --yes train.py`) also imports libraries and executes the selected script without a sandbox; it reports ordering only for supported module-level Python, NumPy, and Torch RNG calls and does not observe generator-instance methods or library-internal/native draws. Read the [security model](https://github.com/QHarshil/adduce/blob/main/docs/security-model.md) before either execution mode.

`adduce pin-remotes` resolves current revisions and drafts `revision="<sha>"` edits as diffs (libcst codemods, applied only with `--write`). Pinning to the *current* SHA is a forward guarantee — it does not recover the version historically used, and the output says so.

## Reviewer time to first result

The reviewer-time estimate uses four buckets: `< 10 min` Excellent · `10–30` Good · `30–90` Risky · `90+` High reviewer burden. It lists the contributing signals (for example, no one-command path, manual data fetch, no smoke target, or undocumented runtime) so the estimate remains inspectable.

## Scoring, profiles, suppression

Scoring is category-weighted and explainable — each category reports earned/possible with the findings that moved it; inapplicable categories drop out and the rest renormalise, so a scikit-learn repository is never scored against CUDA flags. Profiles: `default`, `neurips`, `iclr`, `acl`, `acm`, `strict`, or your own TOML.

Scores and named tiers are experimental prioritisation aids. They are not
calibrated quality grades and should not be used to rank unrelated repositories;
repositories can have materially different applicable-rule denominators.

Every finding carries four separate dimensions — status, confidence, severity, and score weight — because a low-confidence high-severity issue (a possible committed secret) must not read the same as a high-confidence low-severity one (a missing `.zenodo.json`).

```python
loader = DataLoader(ds, shuffle=True)  # adduce: ignore=R-DET-004
```

```toml
[tool.adduce]           # or adduce.toml
profile = "neurips"
ignore = ["R-ARC-001"]
exclude = ["third_party"]
```

Suppressed findings still appear, marked as ignored, and retain their observed
score. Suppression records an accepted exception; it does not count missing
evidence as a pass. Repository-configured exclusions reduce scan scope and are
disclosed in reports. `--mode reviewer` and `--mode ae-chair` bypass
repository-supplied profile, ignore, exclude, and threshold settings unless the
caller supplies explicit CLI options.

## Continuous integration

The default run is diagnostic: `adduce check` exits 0 regardless of score. Gate with `--fail-under N`, or adopt incrementally with `adduce baseline` + `--fail-on-regression`, which fails only when a recorded rule gets *worse* than the committed `.adduce/baseline.json`. Rules absent from the baseline are not classified as regressions.

```yaml
# .github/workflows/reproducibility.yml
name: reproducibility
on: [pull_request]
jobs:
  adduce:
    permissions:
      contents: read
      security-events: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: QHarshil/adduce@v0.1.2
        with:
          profile: neurips
          report-file: adduce-report.md   # lands in the job summary
          sarif-file: adduce.sarif
      - uses: github/codeql-action/upload-sarif@v3   # code-scanning alerts on public repos
        if: always()
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

## Generation safety

adduce generates checklist and appendix drafts that may enter real submissions, so their answers are derived from a deterministic evidence ledger—never treated as final claims or substitutes for author review. The full [generation-safety contract](https://github.com/QHarshil/adduce/blob/main/docs/generation-safety.md) documents this policy; the short version:

- Generated answers use a fixed vocabulary — `yes` (direct, high-confidence evidence), `partial` (incomplete, inferred, or conflicting evidence), `not detected` (searched and absent, with the search scope recorded), `author input required` (depends on information outside the repository), `unknown` (too ambiguous to classify). There is no unsupported "yes."
- Every checklist and appendix generation updates `.adduce/evidence-ledger.json`: per-answer evidence with available `file:line` anchors, confidence, evidence strength, and generation provenance (version, command, profile, commit, timestamp). Generated text is downstream of deterministic evidence, not the source of truth.
- `--strict-evidence` tightens generation for authors who want zero inference in the output.
- Checklist, appendix, and package generation end with a safety summary (evidence-backed vs. partial vs. author-input answers, conflicts, the ledger path)—a draft with open items is useful, but it is not submission-ready, and adduce says so.
- `adduce audit-generated <artifact>` checks a generated artifact against its ledger before submission: unsupported claims, low-confidence yeses, execution wording that cannot be supported by checklist/appendix ledgers, unresolved provider prose and placeholders, and drift since the ledger was produced. Dynamic reports remain separate and are never imported as submission evidence.
- Checklist and appendix drafts do not imply execution-based verification; `adduce reproduce` writes a separate dynamic report. Nothing is invented from context; conflicts are surfaced rather than silently resolved; a detected likely-credential finding omits the matched value; source is never edited without an explicit `--write` after a shown diff. Generated drafts and dynamic reports are not general-purpose secret scrubbers and must be reviewed before sharing.

## Optional LLM layer

Checks, scores, and checklist answers remain deterministic and offline. With a configured provider (`ADDUCE_LLM_PROVIDER=openai|anthropic|ollama`, bring your own key or a local model), `adduce checklist --llm` can draft optional free-text justification from finding summaries. Provider prose is labelled as unverified, carries an author-review marker, and never counts as evidence or determines the ledger answer. The ledger records the provider, model, and a hash of each prose fragment without recording credentials. Without a provider, everything works identically. Adduce ships no key and makes no provider request unless the user explicitly selects `--llm` and configures a provider.

## Honest limits

- **Signals, never certification.** adduce reports what it detected and what it could not; it never says "your code is reproducible", and it never assesses execution-based badges (Results Reproduced/Replicated).
- **Automatic claim inference is scaffolding.** Reliable claim trails currently
  require author-confirmed manifest claims; inferred repository-wide candidates
  may be missing or unrelated to the headline result and must not be treated as
  supported claims.
- **Static resolution has a ceiling.** Alias plus one-hop wrapper resolution handles the explicitly supported call shapes; coverage on unfamiliar ML repositories has not yet been established. Python's dynamism is not generally resolvable statically, so uncertain evidence is reported with confidence and can require a separately authorized dynamic check.
- **The probabilistic rules are diagnostic.** LaTeX numeric extraction, result reconciliation, notebook staleness, and ablation matching will sometimes miss or over-flag; they carry confidence and stay off the blocking path by default.
- **Remote pinning is a forward guarantee**, not recovery of the version historically used.
- **Dynamic reproduction is not a sandbox.** The copied workspaces separate run inputs, but the repository command retains the invoking user's host access and permissions.
- **Not a secret scrubber.** Likely-credential findings redact the matched value,
  but generated drafts can include repository-derived commands, paths,
  identifiers, and metadata, and reproduction reports retain the selected
  command and parsed metric names and values.
- **CUDA/cuDNN versions are rarely in source.** adduce checks whether anything *captures* them (container, conda env, manifest), not that it can read them from code.
- **Not a data-leakage detector.** Train/test contamination is undetectable statically and adduce claims nothing about it.
- **No project-operated backend.** Built-in checks run locally; only explicitly selected remote-metadata or provider features make network requests.

## Development

```bash
git clone https://github.com/QHarshil/adduce
cd adduce
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,release]"
pytest --cov=adduce --cov-report=term-missing --cov-fail-under=85
ruff check src tests scripts corpus/scripts
mypy src/adduce scripts corpus/scripts
python -m build
twine check --strict dist/*
```

The real-repository corpus protocol defines a pending release quality gate; no
effectiveness or calibration claim is made until its human-review requirements
are complete. See the [validation corpus protocol](https://github.com/QHarshil/adduce/blob/main/corpus/README.md) and
permitted conclusions. Contributions are welcome, especially incorrect or
low-value finding reports. See [CONTRIBUTING.md](https://github.com/QHarshil/adduce/blob/main/CONTRIBUTING.md). Report
vulnerabilities privately under the [security policy](https://github.com/QHarshil/adduce/blob/main/SECURITY.md).

## License

[MIT](https://github.com/QHarshil/adduce/blob/main/LICENSE)
