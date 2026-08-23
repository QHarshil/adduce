# synthetic_metric_vocabulary

A paper that heads its result columns with names only the extended metric
vocabulary can read, in the three shapes that decide what a cell is called.

The first table is LoRA's shape, and it is a repair rather than a reach. Its
caption names BLEU, and the columns beside `BLEU` name MET, CIDEr and TER. A
column heading no metric this build knows falls back to the one its caption
states, which is right for a table whose columns are corpora and wrong here: on
LoRA it renamed 22 cells to `bleu`, and knowing those names reads the same
columns as `meteor`, `cider`, `ter` and `rouge_l` instead. The same defect in
miniature reads this paper as claiming BLEU four times — 70.4, 46.8, 2.53 and
0.31 — three of them stating a number the paper really prints under a metric it
never measured. `BLEU` itself is the control: it names a metric the vocabulary
already had, and it keeps that name and full confidence whatever happens beside
it.

The second table is T5's shape. `ROUGE-1`, `ROUGE-2` and `ROUGE-L` are three
summarisation metrics printed side by side in one row, and a vocabulary
resolving all three to `rouge` turns that row into one metric holding three
different values, which reads as a contradiction rather than as three results.
It is the distinctness rule AP50 and AP75 already answer to, and ROUGE violated
it until the split.

The third table carries a pure alias. `SCC` is how a paper heads the Spearman
correlation of STS-B, and `spearman` is a name the vocabulary already had, so the
alias introduces no metric and decides only whether that column can be read at
all. `MCC` beside it is the other correlation and a name the same change added:
two correlations in adjacent columns of one GLUE row, which is why they may not
share a canonical name.

Read correctly, the paper states `bleu = 70.4`, `meteor = 46.8`, `cider = 2.53`
and `ter = 0.31`; `rouge_1 = 43.52`, `rouge_2 = 21.55` and `rouge_l = 40.69`;
and `spearman = 88.7` and `matthews = 62.1`. Nine claims from nine cells, every
one named by its own header at `direct_parse` / 1.0, and `results/eval.csv`
states the CIDEr score so that one claim resolves to a log.

**This case is exercised by the corpus byte-identity comparisons, not by
`expectations.yaml`.** The LaTeX collector does run there:
`tests/test_synthetic_corpus.py` calls `run_check` with no paper path, and the
collector finds `paper/main.tex` inside the case directory either way, so the
paper is read and its cells are parsed. What no rule reads is a table cell or a
drafted claim, so no fix to how a cell is read can move a verdict. The
expectations entry therefore pins only that a paper whose cells reach no rule
produces no verdict rather than a wrong one; the entry for
`synthetic_wrapped_table_header` in `../expectations.yaml` states the caveat in
full. That each of these names
canonicalises, and that the ROUGE variants and the two correlations stay
distinct, is asserted directly by `tests/test_claims_extraction.py`.

It exists because those comparisons were **vacuous** for the whole vocabulary
class. Reverting the extension in full — twenty-six canonical names and two
aliases, against a source tree identical in every other respect — leaves every
other case byte-identical, because no other synthetic paper prints any name it
added. That is a true negative, not evidence. It moves this one, on all nine
claims. The instrument itself is live: disabling canonicalisation outright moved
nine cases, measured at the twenty-one the corpus held when this was added.

The case also moves under each of the three parts separately, measured with
`bench/dev/manifest_identity.py` against a copy of the source tree carrying only
that one mutation:

- Removing the `cider`, `meteor` and `ter` groups leaves those three columns to
  the caption, so `meteor = 46.8`, `cider = 2.53` and `ter = 0.31` become three
  more `bleu` claims at `lexical_match` / 0.5 and `cider`'s `results/eval.csv`
  resolution is lost with them. `bleu = 70.4` is unmoved.
- Removing `scc` from the `spearman` group renames that claim to `scc` and drops
  it to `lexical_match` / 0.5. Nothing else in the paper moves.
- Collapsing `rouge-1`, `rouge-2` and `rouge-l` back onto a single `rouge`
  leaves three confidently read claims that all name `rouge` and disagree.

Each of the three moves this case and none of them moves any other case in the
corpus. Each also moves the default JSON report,
because all three change a metric's name; the confidence and resolution-method
half of the first two is visible in the manifest alone.

```console
adduce check corpus/synthetic/synthetic_metric_vocabulary \
  --paper corpus/synthetic/synthetic_metric_vocabulary -f json
```

to see the drafted claims, and

```console
adduce manifest corpus/synthetic/synthetic_metric_vocabulary \
  --paper corpus/synthetic/synthetic_metric_vocabulary
```

for the confidence and resolution method the JSON report drops.
