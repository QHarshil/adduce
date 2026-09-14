# synthetic_quoted_baseline_rows

Two results tables in which some rows report numbers the paper credits to prior
work, marked the two ways LaTeX markup can mark them. One of the two marks is
detected here and the other is not, and the case pins both.

The reading is correct either way — the header names a metric and the cell
states a value. What is wrong is offering a competitor's result as a claim
about this artifact.

## The signal that is detected

A **citation in the row label** names the paper a row came from. `BiDAF` and
`Swin-T` carry one, in the two spellings that differ most (`\cite` and
`\citep`), and the cells beside them drop to `lexical_match` at `0.5`.

The detection happens before the cell cleanup runs, because the cleanup
dissolves the command and leaves the bibliography key against the label, where
nothing can tell it from part of a model's name — which is why the row arrives
as `BiDAF~seo2017bidirectional`. Reading the flag from the raw row and cleaning
the label from a copy is what stops dissolving a citation from promoting a
quoted baseline to a confident own result.

## The signal that is not

A **full-width section row** partitions a table into senses. The first table
here is BERT's SQuAD shape: `Published` marks the rows beneath it as somebody
else's and `Ours` marks the end of that stretch. `R.M. Reader` is the row that
matters, because it carries no citation at all — only the section above it says
whose number that is.

This build does not read section rows, so `R.M. Reader` keeps `direct_parse` at
`1.0` on both its cells. That is the miss
[`docs/claims.md`](../../../docs/claims.md) names, recorded here as a fixture
rather than only as prose, so that a later build which closes it moves this case
and says so.

The second table is the control for what a section row must not do. It is
partitioned in exactly the same way, by `ImageNet-22K pre-trained`, which names
a pre-training corpus and says nothing about who produced anything. An
unrecognised heading is the common case by a wide margin, so it must clear the
sense rather than continue it and rather than mean "baseline": either would
demote a paper's own results across whole tables. `TinyNet` beneath it keeps
`direct_parse` at `1.0`, exactly as `TinyNet` beneath `Ours` does, while
`Swin-T` beside it is demoted by its citation alone.

## Demotion, never removal

Every one of the ten cells is still a claim, at the same metric and the same
value and the same location, and only how confidently it was read moves. That is
deliberate. The detector is a heuristic over markup, so a false positive in it
would silently destroy a real own result — the more expensive mistake — and
demotion costs no recall, because matching a claim to a reported number reads
the metric and the value and never the confidence.

## Where this case is asserted

`expectations.yaml` pins only that a paper whose cells reach no rule yields no
verdict rather than a wrong one. Table cells reach the claims package alone and
no rule reads a drafted claim, and this case carries no results file, so the two
reconciliation rules abstain on that ground as well.

**The `--paper` byte-identity comparison cannot see this case, and that is worth
stating rather than discovering.** The default JSON report carries a claim's
metric, value, location and trail and carries neither its confidence nor its
resolution method, so every synthetic case renders identically across a change
that moves only those two fields — a true negative, not a check.

What does carry the demotion is the drafted manifest, which records both fields:

```console
adduce manifest corpus/synthetic/synthetic_quoted_baseline_rows \
  --paper corpus/synthetic/synthetic_quoted_baseline_rows
```

Ten drafted claims: four at `confidence: 0.5` / `resolution_method:
lexical_match`, the two cells of each cited row, and six at `1.0` /
`direct_parse`. [`bench/dev/manifest_identity.py`](../../../bench/dev/README.md)
is the instrument that reads that manifest, and
`tests/test_bench_dev_manifest_identity.py` uses this case to prove the
instrument is live: it removes the demotion from a copy of `src` and asserts the
harness reports this case moved, on `confidence` and `resolution_method` alone.
