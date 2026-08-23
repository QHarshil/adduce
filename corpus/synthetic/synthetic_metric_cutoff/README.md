# synthetic_metric_cutoff

A paper that reports retrieval and summarisation, where the metric's name ends
in the rank it was measured at, in the three places that were read as results.

`Recall@1` names a metric. The `1` says at which rank the recall was measured
and not what the recall was, so it is part of the name, exactly as the `10` of
`CIFAR-10` is. The guard refusing a number glued to a word held for letters, for
`-` and for `_`, and not for `@`.

The abstract sentence is BLIP's, whose own numbers are `+2.7` and `+2.8` — two
improvements, neither of them a result — and whose `recall@1` was read as a
recall of 1. BLIP states ten candidates of this shape, and they arrive by two
routes that need answering together: the prose pattern `\brecall\b` leaves the
`@` ahead of the number, while the pattern `recall@` takes the `@` into the
match, where the number is adjacent to the keyword and nothing sits between them
at all. Closing one route leaves the same sentence stating the same false number
through the other.

The first table is BLIP's VisDial header, and it is the case for reading a header
row as prose at all: `MRR$\uparrow$ & R@1$\uparrow$` was read as an MRR of 1,
while the cells beneath it are the real MRR, R@1 and R@5. Its caption carries the
third route, `B@4: BLEU@4`, read as a BLEU of 4 — beside a table that really does
report `B@4`, at 38.2.

The second table is where the defect was first written down: a header row reading
`ROUGE-L & B@4` emits `rouge = 4.0`, because the `rouge` prose pattern matches
the header and the search that follows it finds the `4` of `B@4` inside its
window. It is the same family as `\multicolumn{1}` read as a throughput of 1.

`recall@1 of 82.5` is the control, and it is why a cutoff is *skipped* rather
than refused. It is how a retrieval paper states a result, and a guard that
rejected the candidate on sight would answer four false positives by losing that
number as well — a miss traded for a false positive, on the commonest shape in
the class. So the rank is stepped over and the number after it is read, within
the same window as before. BLIP's own four sentences state their numbers *before*
the cutoff, so nothing there is recovered or lost either way; this case is where
that difference is visible.

Read correctly, the paper states `MRR = 69.4`, `recall_at_1 = 56.1` and
`recall_at_5 = 87.3` from the first table, `ROUGE-L = 40.7` and `B@4 = 38.2`
from the second, and `recall = 82.5` from the sentence that states it. Six
claims, and the two tables name every one of their columns.

**This case is asserted in `expectations.yaml`.** It carries `results/eval.csv`
stating the recall its own sentence states, so the two reconciliation rules that
read the paper's prose numbers have something to reconcile against, and reading a
cutoff as a value is then not merely a spurious claim. Against a source tree that
reads cutoffs, and identical in every other respect, two of the 28 rules move:

- **R-RES-002 `partial`** — "Reported metric(s) materially differ from the logged
  values: recall: paper 1 vs closest logged 82.5". The paper is reported as
  contradicting its own results, where what it states is 82.5 and the 1 is a rank.
- **R-RES-004 `partial`** — `bleu`, `mrr` and `rouge` reported as extracted
  metrics with no logged column, all three read out of a cutoff, two of them
  contradicting the correctly read `bleu = 38.2` and `rouge_l = 40.7` beside them.

The corpus byte-identity comparisons see the whole claim set behind those
verdicts, and they were **vacuous** for this class before this case: no other
synthetic paper writes a metric with a cutoff, so every other case is
byte-identical whether the cutoff is read or not. This case moves, and it is the
only case that moves, on `bench/dev/manifest_identity.py` and on the default JSON
report alike — measured at the 24 cases the corpus held when this was added.
Reading cutoffs gives it **9 claims instead of 6** — `recall = 1`, `mrr = 1`,
`bleu = 4` and `rouge = 4`, each at confidence 0.5 — and the sentence's
`recall = 82.5` is read as `recall = 1` instead.

That each cutoff is left unread, that both routes to one are closed, and that the
number stated after a cutoff is still read, is asserted directly in
`tests/test_collectors_new.py`.

Measured on the dev set, the same change removes ten candidates, all of them
BLIP's, and adds none: `recall@1` read as a recall of 1 six times, `BLEU@4` as a
BLEU of 4, and the header's `R@1` as an MRR of 1. BLIP's claim count falls 271 to
268 and its recall-frame false positives 190 to 188, with pooled recall unchanged
at 119/296 and no pair falling.

One residual is worth knowing, and it is not this change's: the sentence's
`recall@1 of 82.5` is drafted as `recall`, where the same name in a *header*
canonicalises to `recall_at_1`. The prose pattern and the header vocabulary
disagree about how specific that name is, so a reader sees `recall = 82.5` at a
line that says `recall@1`. The value is right and checkable; the name is coarser
than the header path's.

```console
adduce check corpus/synthetic/synthetic_metric_cutoff \
  --paper corpus/synthetic/synthetic_metric_cutoff -f json
```

to see the drafted claims, and

```console
adduce manifest corpus/synthetic/synthetic_metric_cutoff \
  --paper corpus/synthetic/synthetic_metric_cutoff
```

for the confidence and resolution method the JSON report drops.
