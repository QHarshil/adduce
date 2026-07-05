# Validation corpus

This directory holds the calibration protocol for adduce's score: two cohorts
of real repositories, a harness that runs adduce over both, and the analysis
conventions for the numbers that may be published.

## Cohorts

Create `repos.csv` with the header `cohort,url,sha` and one row per repository:

- **badged** (~25): repositories from papers awarded ACM "Artifacts Evaluated
  (Functional/Reusable)" badges or the equivalent from venue artifact tracks.
  Prefer Evaluated/Reusable over "Available" — "Available" certifies only
  archival, not reproducibility, and adds noise.
- **unvetted** (~25): typical, unreviewed ML repositories found by searching
  GitHub for a common stack. These stand in for the median artifact a
  reviewer actually receives.

Pin every row to the commit SHA at selection time so the validation run is
itself reproducible.

## Running

```bash
python corpus/run_validation.py
```

Writes one JSON per repository under `corpus/results/` and a combined
`corpus/results.csv` with the total, per-category percentages, reviewer-time
bucket, and finding counts. Clone failures and timeouts are logged and
excluded, never silently dropped.

## What may be claimed, and from what

1. **Score separation** (automated): compare the cohorts' score
   distributions — medians, ranges, gap, overlap. With ~50 repositories this
   is a distributional signal; report medians and spread, never invented
   significance statistics.
2. **False-positive rate** (manual): sample ~10 repositories across both
   cohorts and hand-label every finding as correct or spurious. The FP rate
   is spurious over total on that sample, reported with the sample size.
   Scores cannot produce this number.

Freeze rule weights before publishing any figure, and record the adduce
version that produced it. If a measurement does not hold up, fix the rules or
narrow the claim — never soften wording around a number that is not real.
