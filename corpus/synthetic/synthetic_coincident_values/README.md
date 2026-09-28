# synthetic_coincident_values

A paper that states one value three times as three different measurements, and
a fourth time as a genuine restatement of one of them.

De-duplicating claims on `(metric, value)` alone reads all four as one claim,
and an own result then becomes a claim attributed to a competitor's row. A
locator cannot separate them, because every cell of one `tabular` records the
line the environment opens on. What separates them is that a candidate's row and
column together name what was measured.

The first table states `84.1` three times: the authors' model on the development
split, the authors' model on the test split, and prior work on the development
split. None restates another, so they must stay three claims.

The second table repeats the full model's development-set exact match, as an
ablation table does. That is one number stated in two places, so it must merge
into one claim that keeps both locations.

Read correctly, the paper states seven claims from eight cells. The restated
`Dev EM = 84.1` for the authors' model is one claim at two locations.

No rule reads a drafted claim, so `expectations.yaml` pins only that the paper
yields no wrong verdict. `tests/test_synthetic_claim_fixtures.py` asserts both
halves directly. A clusterer that never separates two measurements, or never
merges two labelled cells, turns that test red. Nothing in the output reports
that a claim was stated twice: `ClaimCluster.restated` computes it and no
reporter reads it.

```console
adduce manifest corpus/synthetic/synthetic_coincident_values \
  --paper corpus/synthetic/synthetic_coincident_values
```

shows the drafted claims.
