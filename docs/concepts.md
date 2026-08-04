# Concepts

## The three layers, and which one this is

The reproducibility problem has three layers. FAIR tools such as `howfairis`
focus on **sharing** (findable, licensed, citable). ReproZip, DataLad, and
repo2docker focus on **packaging** (capture and replay execution). `adduce`
focuses on **traceability**: whether each reported claim maps to the code,
config, data, seed, environment, command, and logged result that produced it,
while using sharing and packaging signals as inputs.

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

A `smoke` target can substantially reduce reviewer setup time by checking the
pipeline's shape without requiring the full experiment.

This is a schema illustration, not captured tool output — see the
[README](../README.md#what-it-reports) for a real captured claim trail.
