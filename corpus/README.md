# Validation corpus

Three layers of evidence, each answering a different question, each with its
own limits on what may be claimed from it.

## Layer A — synthetic positive controls (`corpus/synthetic/`)

Fourteen tiny repositories, each isolating one behaviour: a real
hyperparameter drift, a rounding-level metric match, a materialised-config
authority flip, a committed secret, and red-team traps (Docker claimed in
prose, seeds claimed but never set, metrics that belong to other methods).
`corpus/synthetic/expectations.yaml` pins what adduce must and must not
report per case, and `tests/test_synthetic_corpus.py` enforces it on every
test run — this is the permanent false-positive regression suite. It proves
the detectors fire (and stay silent) where designed; it says nothing about
real-world rates.

## Layer B — labelled real corpus (~50 repositories)

Rows live in `repos.csv` with the header:

```
id,cohort,repo_url,commit_sha,badge_type,venue,year,framework,has_tex,notes
```

Cohorts (`commit_sha` empty means HEAD; the clone step records the resolved
SHA in `corpus/clones/clones_manifest.json` so every run stays attributable):

- **badged (~25 total)**, split so badge strength is visible instead of
  averaged away:
  - `badged_functional` — ACM "Artifacts Evaluated (Functional/Reusable)".
    The strongest signal: a committee ran the artifact.
  - `badged_available` — ACM "Artifacts Available" only. Certifies archival,
    not reproducibility; kept separate precisely because it adds noise.
  - `badged_venue` — venue artifact tracks (NeurIPS/MLSys/ACL badges and
    equivalents).
- **unvetted (~25)**: typical unreviewed ML repositories chosen by
  stratified sampling, 5 each of: small student repos, medium lab repos,
  tutorial-style repos, older ML (pre-2020), and modern HF/PyTorch stacks.
  These stand in for the median artifact a reviewer actually receives.

## Layer C — unlabelled stress corpus

50–100 popular or messy repositories (`cohort=stress`; seeded with nanoGPT,
minGPT, vit-pytorch). Tracked measurements: crash rate, runtime, and the top
noisy rules. This layer exists to find robustness problems and chatty rules,
**never** to back scoring claims — stress scores appear in no published
number.

## Running

```bash
python corpus/scripts/clone_repos.py --repos corpus/repos.csv --out corpus/clones
python corpus/scripts/run_validation.py --repos corpus/repos.csv --clones corpus/clones \
    --out corpus/outputs/<adduce-version> --timeout 120
python corpus/scripts/summarize.py --run corpus/outputs/<adduce-version>
python corpus/scripts/sample_findings.py --run corpus/outputs/<adduce-version> \
    --n-repos 12 --out corpus/labels/findings_sample_<adduce-version>.jsonl
python corpus/scripts/label_findings.py corpus/labels/findings_sample_<adduce-version>.jsonl
python corpus/scripts/label_findings.py corpus/labels/findings_sample_<adduce-version>.jsonl --report
```

The run step writes `raw_json/<id>.json` per repository plus `combined.csv`
(score, tier, reviewer-time bucket, per-category percentages, fail/partial
counts). Clone failures, crashes, and timeouts are logged and recorded as
rows with `crash=true` — never silently dropped. Layer A needs no separate
command; it runs with the test suite (`pytest tests/test_synthetic_corpus.py`).

## What may be claimed, and from what

1. **Score separation** (automated, layer B): compare the cohorts' score
   distributions — medians, IQRs, ranges, overlap. With ~50 repositories
   this is a distributional signal; report medians and spread, never
   invented significance statistics.
2. **False-positive rate** (manual, layer B): hand-label the stratified
   finding sample (`true_positive_actionable`, `true_positive_minor`,
   `false_positive`, `unclear_unverifiable`, `low_value_noise`) and report
   the actionable/FP/unclear rates with the sample size. Scores cannot
   produce this number.
3. **Robustness** (layer C): crash rate, runtime, top noisy rules. Nothing
   else.

Freeze rule weights before publishing any figure, and record the adduce
version that produced it (the run harness writes it into `run_meta.json`).
If a measurement does not hold up, fix the rules or narrow the claim — never
soften wording around a number that is not real.
