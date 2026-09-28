# synthetic_metric_cutoff

A paper reporting retrieval and summarisation, where a metric's name ends in the
rank it was measured at.

`Recall@1` names a metric. The `1` says at which rank the recall was measured,
not what the recall was, so it is part of the name, as the `10` of `CIFAR-10`
is. The guard refusing a number glued to a word held for letters, `-` and `_`,
and not for `@`.

The cutoff reaches the extractor by two routes that must be closed together. The
prose pattern `\brecall\b` leaves the `@` ahead of the number, while the pattern
`recall@` takes the `@` into the match, where nothing separates the keyword from
the number. The paper carries both, plus a header row (`MRR$\uparrow$ &
R@1$\uparrow$`) read as an MRR of 1, a caption (`B@4: BLEU@4`) read as a BLEU of
4, and a header row `ROUGE-L & B@4` whose prose pattern finds the `4` of `B@4`.

`recall@1 of 82.5` is the control, and it is why a cutoff is skipped and not
refused. It is how a retrieval paper states a result, and a guard that rejected
the candidate on sight would lose that number. The rank is stepped over and the
number after it is read, within the same window as before.

`results/eval.csv` logs the recall the sentence states, so the reconciliation
rules have something to reconcile against. Measured on this line with the skip
removed, the case reads `recall = 1`, `mrr = 1`, `bleu = 4` and `rouge = 4`,
and two rules move:

- **R-RES-002 `partial`**: the paper's recall of 1 is reported as materially
  differing from the logged 82.5.
- **R-RES-004 `partial`**: `bleu`, `mrr` and `rouge`, all read out of a cutoff,
  are reported as extracted metrics with no logged column.

With the skip, the only prose value read is `recall = 82.5`, and both rules pass.
`tests/test_collectors_new.py` asserts that both routes are closed and that the
number after a cutoff is still read.

One residual: the sentence's `recall@1 of 82.5` is drafted as `recall`, where
the same name in a header canonicalises to `recall_at_1`. The value is right and
checkable; the name is coarser than the header path gives.
