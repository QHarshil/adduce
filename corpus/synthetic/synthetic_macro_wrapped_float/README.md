# synthetic_macro_wrapped_float

A paper that wraps a whole results table in a command and invokes it in the
document body — the CVPR/ICML idiom under which a definition's body *is* the
page — alongside one definition that really is only a name.

`\segmentationtable` holds a complete `table` float: a `tabular` stating an IoU
and a Dice for two models, its caption, and its label. Nothing about it is
shown where it is defined and everything about it is shown where
`\segmentationtable` is written, twelve lines further down. Expansion cannot
move it: one textual pass cannot place an environment, which is why
`_zero_argument_macros` refuses an environment-bearing body outright. So a pass
that removes the definition removes the table from the paper and puts nothing
back.

`\iouloss` is DETR's shape and the contrast the case exists to draw. Its body
states a metric name against the parameter placeholder the command will be
given, so `{\cal L}_{\rm iou}(#1)` is read as an IoU of 1 — a number in no
rendered document. It is removed, and it is *parameterised*, which is exactly
what expansion refuses. The two together are why the test cannot be "keep
whatever expansion would accept": that gate keeps the artifact and destroys the
table.

Read correctly, the paper states `IoU = 82.9` and `Dice = 91.3` from its caption
and four table cells — `Baseline` 74.1/85.6 and `Ours` 82.9/91.3.

**This case is asserted in `expectations.yaml`, and that is unusual for a
paper-side case.** Table cells reach only the claims package, which no rule
reads, so a cells-only fixture cannot move a verdict. This one carries
`results/eval.csv` stating the IoU and Dice the caption states, so the two
reconciliation rules have something to reconcile against — and the case then
bites in **both** directions on a single rule:

- Against a source tree that removes an environment-bearing definition, the
  paper states nothing at all: **R-RES-002 `unknown`** — "No paper metric could
  be matched to a logged metric column by name" — and **R-RES-004
  `not-applicable`** — "No metric statements extracted from paper or manifest".
  Measured; that is the state this fixture was written against.
- Against a source tree that removes no definition, `\iouloss` is read as an IoU
  of 1 and **R-RES-002** goes `partial`, reporting the paper as contradicting
  its own logs on a number it never states.

Both are excluded by `R-RES-002: pass` plus `R-RES-004: pass`, and the `unknown`
direction is forbidden by name because it is the one that destroys content.

The damage this guards against was measured on two unlabelled development pairs,
and it is why the case is here rather than only in `tests/`: the recall gate sees
the 20 labelled pairs, both damaged papers are unlabelled, and the defect
survived because nothing looked at them. `latent-diffusion` went from **624
table cells to 0** and `stylegan2-ada` from **66 to 0** — 20,616 of the 20,647
characters of `ms_tables_supp.tex` and 48,434 of the 48,635 of
`supplemental-figures.tex` replaced by their own line breaks, taking with them
`Batch Size 48`, `Learning Rate 1.0e-4`, `Heads 8`, `Recall 0.261` and all 236
of latent-diffusion's prior-work cells.

That the definitions are what moves this case, rather than something else a
source tree changed, is asserted directly in `tests/test_collectors_new.py`:
the float kept, the parameterised float kept, a float body past the expansion
length bound kept, a markup definition written *inside* a kept float still
removed, and DETR's, llncs' and BERT's three shapes still removed.

The cost of keeping these bodies is also measured, and it is an index rather
than content. A kept `tabular` is parsed as a table in its own right, so the
real ones are numbered from further along: on MoCo, 12 such phantom tables over
19 real ones, with its 260 cells identical either way — `-0 +0` on the multiset
of `(row, column, value, file, line)`. `table_index` is read by nothing outside
the collector.

```console
adduce check corpus/synthetic/synthetic_macro_wrapped_float \
  --paper corpus/synthetic/synthetic_macro_wrapped_float -f json
```

to see the drafted claims, and

```console
adduce manifest corpus/synthetic/synthetic_macro_wrapped_float \
  --paper corpus/synthetic/synthetic_macro_wrapped_float
```

for the confidence and resolution method the JSON report drops.
