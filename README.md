# adduce

[![CI](https://github.com/QHarshil/adduce/actions/workflows/ci.yml/badge.svg)](https://github.com/QHarshil/adduce/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/adduce.svg)](https://pypi.org/project/adduce/)
[![Python versions](https://img.shields.io/pypi/pyversions/adduce.svg)](https://pypi.org/project/adduce/)
[![License: MIT](https://img.shields.io/github/license/QHarshil/adduce.svg)](https://github.com/QHarshil/adduce/blob/main/LICENSE)

**A local research-artifact auditor.**

`adduce` checks whether a paper's claims, code, configs, data, dependencies, remote models, precision settings, and generated results still agree with each other before submission. It also drafts repository-observable NeurIPS/ACL checklist items, an ACM Artifact Appendix, archival metadata (RO-Crate, Croissant, CodeMeta, Zenodo), and a claim-by-claim evidence trail for author review.

The north-star question: *for every number in the paper, can I point to the artifact that produced it, and will that artifact still produce it elsewhere?*

## Install

```console
pipx install adduce        # or: pip install adduce / uvx adduce
adduce check .
```

[PyPI `0.1.2`](https://pypi.org/project/adduce/0.1.2/) is the current release.

Upgrade commands per installer, and the caveat that existing installations do
not update themselves, are in [docs/cli-reference.md](https://github.com/QHarshil/adduce/blob/main/docs/cli-reference.md#installing-and-upgrading).

Adduce is beta software. Findings are static-analysis signals for review, and
scores, tiers, and reviewer-time estimates are provisional pending calibration
against manually reviewed real repositories. Generated submission material is
always a draft.

> Built-in checks are offline by default. Public-metadata requests require the
> explicit `--online` or `pin-remotes` modes and use a bounded public-HTTPS
> resolver; pre-existing cache entries never count as network evidence. Read
> the [security model](https://github.com/QHarshil/adduce/blob/main/docs/security-model.md) before using network, provider,
> plugin, or dynamic-execution features on untrusted input.

## What it reports

Verbatim excerpt from `adduce check` on [nanoGPT](https://github.com/karpathy/nanoGPT)
at commit `3adf61e`. Omitted whole: eight of the fifteen category rows, the
no-paper-sources notice, the inferred claim-trail block, the last two fixes, and
the closing `Next:` line. Nothing is reworded, and every `…` is the tool's own
truncation marker.

```
╭─ adduce  ·  nanogpt  ·  commit 3adf61e ────────────────────────────────────────────────────────╮
│ Reproducibility  54/100   Bronze   ·   profile: default                                        │
╰────────────────────────────────────────────────────────────────────────────────────────────────╯
Reviewer time to first result: 23–83 min (Risky)
  - no one-command reproduction path
  - environment must be assembled by hand (no container or conda env)
  - dependency resolution may not converge to the original environment
  - no smoke/quick-run target for a minutes-scale sanity check

Category                        Score  Notes
Code & Execution                 8/12  Commands are documented, but there is no run script or
                                       Makefile target to execute them; The README shows run
                                       command(s) (e.g. `python
                                       data/shakespeare_char/prepare.py`), but …
Environment & Tooling            1/10  No dependency manifest found (requirements.txt,
                                       pyproject.toml, environment.yml); No lockfile found
                                       (poetry.lock, uv.lock, Pipfile.lock, conda-lock); No
                                       Dockerfile, …
Data                             8/10  No checksums or content-addressed data tracking detected; A
                                       data directory exists but does not separate raw from
                                       processed content
Determinism & Model              3/12  Some RNG sources are seeded, but not all: missing python
                                       (random.seed), numpy (np.random.seed or default_rng);
                                       Neither torch.backends.cudnn.deterministic=True nor …
Numerical Precision & Hardware    2/4  TF32 / float32-matmul precision control in use
                                       (torch.backends.cuda.matmul.allow_tf32 = True;
                                       torch.backends.cudnn.allow_tf32 = True) but no precision
                                       policy is documented …
Result Reconciliation               —  1 check(s) applied; none could be assessed
Portability                       3/3  all detected checks satisfied

Top fixes (largest score gains first)
 1. Extend the seeding helper to cover: python (random.seed), numpy (np.random.seed or
default_rng).
     adduce fix --scaffold seeds
 2. Set torch.backends.cudnn.deterministic = True and torch.backends.cudnn.benchmark = False in
the seeding helper.
     adduce fix --scaffold seeds
 3. Declare dependencies, then pin them (pip freeze, pip-compile, uv lock, poetry lock).

Statuses are detected signals from static analysis, not a certification of reproducibility.
```

Location-bearing findings are anchored to source lines—the TF32 finding above
points at `train.py:107`, and the unpinned hub call at `model.py:238`. Seven of
the fifteen categories that applied to nanoGPT are shown above; the same run
also covers Documentation, Run Traceability, Checkpoint & Experiment State,
Notebooks, Remote Artifacts & Rot, Versioning, Access & Legal, and Archival
Readiness.

When a manifest declares claims, the report adds a per-claim trail. Trimmed
output from `adduce manifest` followed by `adduce check` on the synthetic
positive-control repository
[`corpus/synthetic/synthetic_rounding_match`](https://github.com/QHarshil/adduce/tree/main/corpus/synthetic/synthetic_rounding_match)
in this repository, where the paper states an accuracy of 81.4 and the logged
run recorded 81.37:

```
Claim trails (manifest; draft claims remain inferred until author-confirmed)
  paper/main.tex:3  ·  "accuracy of 81.4" [inferred draft]
    metric      results/eval.csv:accuracy  (found: 81.37)   ~ rounding vs paper
(81.4) ✓
    log         results/eval.csv ✓
    status      PARTIAL
```

Every finding carries a status (`pass` / `partial` / `fail` / `not-applicable`
/ `unknown`), a confidence, available file:line locations, and a concrete
remediation. `partial` is used when the repository supports only part of a
check.

## Honest limits

adduce reports detected signals, never a certification of reproducibility. It
never says a repository "is reproducible", and it never assesses
execution-based badges (Results Reproduced/Replicated) — only badge
*eligibility* signals. Static analysis never implies execution: the opt-in
`--online`/`pin-remotes` (network) and `reproduce` (execution) layers are the
only parts of adduce that leave the offline, static default, and both are
explicitly fenced.

Scores and named tiers are experimental prioritisation aids, not calibrated
quality grades. The [validation corpus protocol](https://github.com/QHarshil/adduce/blob/main/corpus/README.md) defines a
pending release-quality gate, and no effectiveness or calibration claim is
made until its human-review requirements are complete.

The full list of limits — automatic claim inference as scaffolding only, the
static-resolution ceiling, remote pinning as a forward guarantee rather than
historical recovery, dynamic reproduction's non-sandboxed execution, and more
— is in [docs/honest-limits.md](https://github.com/QHarshil/adduce/blob/main/docs/honest-limits.md).

## Documentation

Adduce ships **78 rules across 17 categories**, each gated on whether it
applies, so an inapplicable category drops out of scoring rather than counting
against a repository. A category that did apply but reached no assessment —
`Result Reconciliation` above — keeps its row and shows `—` rather than a
zero, because "nothing to check here" and "checked and found nothing" are
different answers and only one of them is a finding.

Full documentation — every rule, the CLI reference, the manifest and
claim-trail model, CI recipes, generation safety, the optional LLM layer, and
the security model — starts at [docs/index.md](https://github.com/QHarshil/adduce/blob/main/docs/index.md). The [rule
reference](https://github.com/QHarshil/adduce/blob/main/docs/rules/README.md) and [honest limits](https://github.com/QHarshil/adduce/blob/main/docs/honest-limits.md)
are the two pages most worth reading before relying on a score.

## Contributing

Contributions are welcome, especially incorrect or low-value finding reports.
See [CONTRIBUTING.md](https://github.com/QHarshil/adduce/blob/main/CONTRIBUTING.md) for the development setup, design
constraints, and how to add a rule. Report vulnerabilities privately under the
[security policy](https://github.com/QHarshil/adduce/blob/main/SECURITY.md).

## License

[MIT](https://github.com/QHarshil/adduce/blob/main/LICENSE)
