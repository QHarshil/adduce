# Continuous integration

The default run is diagnostic: `adduce check` exits 0 regardless of score.
Gate with `--fail-under N`, or adopt incrementally with `adduce baseline` +
`--fail-on-regression`, which fails only when a recorded rule gets *worse*
than the committed `.adduce/baseline.json`. Rules absent from the baseline
are not classified as regressions.

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
      - uses: QHarshil/adduce@v0.2.0
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
