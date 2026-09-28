# synthetic_metric_vocabulary

A paper whose result columns are headed by names only the extended metric
vocabulary can read, in three shapes.

- **A caption naming one metric over columns naming others.** The caption names
  BLEU and the columns beside `BLEU` are MET, CIDEr and TER. A column heading no
  known metric falls back to the one its caption states, so without these names
  the paper reads as claiming BLEU four times. `BLEU` is the control: it keeps
  its name and full confidence either way.
- **Metrics that must stay distinct.** `ROUGE-1`, `ROUGE-2` and `ROUGE-L` sit
  side by side in one row. Resolving all three to one `rouge` turns three
  results into one metric holding three values.
- **A pure alias beside a new name.** `SCC` is how a paper heads the Spearman
  correlation, and `MCC` beside it is the Matthews correlation. Two correlations
  in adjacent columns may not share a canonical name.

Read correctly, the paper states `bleu = 70.4`, `meteor = 46.8`, `cider = 2.53`,
`ter = 0.31`, `rouge_1 = 43.52`, `rouge_2 = 21.55`, `rouge_l = 40.69`,
`spearman = 88.7` and `matthews = 62.1`. That is nine claims, each named by its
own header at `direct_parse` / 1.0. `results/eval.csv` states the CIDEr score so
that one claim resolves to a log.

No rule reads a drafted claim, so `expectations.yaml` pins only that the paper
yields no wrong verdict. `tests/test_synthetic_claim_fixtures.py` asserts the
nine claims directly. Removing the `scc` alias, the `met` alias or the `rouge_1`
group each turns that test red.

```console
adduce manifest corpus/synthetic/synthetic_metric_vocabulary \
  --paper corpus/synthetic/synthetic_metric_vocabulary
```

shows the drafted claims with their confidence and resolution method.
