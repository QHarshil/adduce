# synthetic_quoted_baseline_rows

Two results tables in which some rows report numbers the paper credits to prior
work, marked the two ways LaTeX markup can mark them. Both marks are detected,
and the case pins each of them, a heading that must not mark anything, and an
own row that a heading must not reach.

The reading is correct either way — the header names a metric and the cell
states a value. What is wrong is offering a competitor's result as a claim
about this artifact.

## A citation in the row label

A **citation in the row label** names the paper a row came from. `BiDAF` and
`Swin-T` carry one, in the two spellings that differ most (`\cite` and
`\citep`), and the cells beside them drop to `lexical_match` at `0.5`.

The detection happens before the cell cleanup runs, because the cleanup
dissolves the command and leaves the bibliography key against the label, where
nothing can tell it from part of a model's name — which is why the row arrives
as `BiDAF~seo2017bidirectional`. Reading the flag from the raw row and cleaning
the label from a copy is what stops dissolving a citation from promoting a
quoted baseline to a confident own result.

## A full-width section row

A **full-width section row** partitions a table into senses. The first table
here opens in BERT's SQuAD shape: `Published` marks the rows beneath it as
somebody else's. `R.M. Reader` is the row that matters, because it carries no
citation at all. Only the section above it says whose number that is, and both
its cells drop to `lexical_match` at `0.5`. `BiDAF` sits under the same heading,
so either signal alone demotes it.

Where BERT then heads its own rows `Ours`, this table puts the paper's own row
after a bare rule, with no heading to end the stretch. A rule is layout and
does not change the sense, so what keeps `TinyNet (ours)` at `direct_parse` and
`1.0` is its label: a row that names itself as the paper's own work is never
demoted by a heading above it. Without that, the first table would demote the
paper's own result.

The second table is the control for a heading the list does not name. Its one
section row is `ImageNet-22K pre-trained`, which names a pre-training corpus and
says nothing about who produced anything. Such a heading is the common case by a
wide margin, so it must not mean "baseline", or it would demote a paper's own
results across whole tables. `TinyNet` beneath it keeps `direct_parse` at `1.0`
with no own-work word in its label, while `Swin-T` beside it is demoted by its
citation alone.

This case does not show that an unlisted heading also clears a mark set above
it. The mark ends with its table, so nothing above `ImageNet-22K pre-trained`
is under `Published`, and a build that let an unlisted heading carry the mark
on would still pass here. `test_a_section_row_naming_no_prior_work_clears_the_mark`
in `tests/test_collectors_new.py` pins the clearing, with `ImageNet-22K
pre-trained` among its headings.

## Demotion, never removal

Every one of the ten cells is still a claim, at the same metric and the same
value and the same location, and only how confidently it was read moves. That is
deliberate. The detector is a heuristic over markup, so a false positive in it
would silently destroy a real own result — the more expensive mistake — and
demotion costs no recall, because matching a claim to a reported number reads
the metric and the value and never the confidence.

## Where this case is asserted

`tests/test_collectors_new.py` reads this case's ten cells directly and asserts
the method and confidence of each.

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

Ten drafted claims: six at `confidence: 0.5` / `resolution_method:
lexical_match`, the two cells of `BiDAF`, `R.M. Reader` and `Swin-T`, and four
at `1.0` / `direct_parse`, the `TinyNet (ours)` and `TinyNet` rows.
[`bench/dev/manifest_identity.py`](../../../bench/dev/README.md) is the
instrument that reads that manifest, and
`tests/test_bench_dev_manifest_identity.py` uses this case to prove the
instrument is live: it removes the demotion from a copy of `src` and asserts the
harness reports this case moved, on `confidence` and `resolution_method` alone.
