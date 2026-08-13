# synthetic_mixed_header_row

A header row that names a metric in one column and a dataset in the others,
with the metric for those datasets in the row beneath.

This is ELECTRA's shape. Its first header row heads one column `Train FLOPs`,
which canonicalises to a metric on its own, and heads the rest with corpus
names whose metric sits in a second header row. A second-header test that asks
whether *no* cell of the first row names a metric refuses such a table
wholesale, so every dataset column keeps the corpus as its name and no cell of
the table can ever match a claim about exact match or F1. Read per column
instead, ELECTRA yields 54 canonically named cells — 27 exact match, 27 F1 —
where the whole-row test yielded none.

The first table here is that shape in miniature. `Accuracy` names a metric
itself and is the mixed cell; `SQuAD` and `MNLI` name corpora and take `F1` and
`EM` from the row beneath. Composition is per column, so the column the paper
already named keeps its own name: a qualifier row cannot rename `Accuracy`
after the fact, and the columns it does name are the ones the first row left
unnamed.

The second table is the control, and it is a refusal rather than a recovery.
Its first row mixes in exactly the same way, so the only thing separating it
from the first table is that the row beneath **states numbers**. A row stating
numbers is data. Reading it as a header would rename three columns after the
values of a result and delete that result from the paper, which is why the
number test is asked first and asked over the whole row rather than per column.
It is what bounds the cost of being wrong in either direction: a row stating no
number yields no cell whichever way it is read, so reading one as a header can
at worst rename the columns beneath it, while reading a row of data as a header
destroys the row.

Read correctly, the first table states `Accuracy = 91.3`, `SQuAD F1 = 88.5` and
`MNLI EM = 84.2`, and the second states six numbers of which two fall under
`Accuracy` and four under `SQuAD` and `MNLI` — columns that name no metric
this build knows, kept as nameable-but-unknown at reduced confidence rather
than guessed at. `results/eval.csv` states the F1, so that one claim resolves to
a log and the other eight do not.

**This case is exercised by the `--paper` byte-identity comparison, not by
`expectations.yaml`.** Table cells reach the claims package and no rule reads
them, and `tests/test_synthetic_corpus.py` calls `run_check` without a paper
path, so the LaTeX collector never runs there. The expectations entry therefore
pins only that an unread paper produces no verdict.

It exists because that byte-identity check was otherwise **vacuous** for the
relaxation from a whole-row test to a per-column one: no other synthetic paper
carries a header row that mixes a metric with dataset names, so all seventeen
targets were identical across the change. That is a true negative, not
evidence. This case moves, and it moves under each half of the change
separately. Restoring the whole-row condition renames `SQuAD F1` to `SQuAD` and
`MNLI EM` to `MNLI`, drops both from a metric this build knows to a lowercased
corpus name at half confidence, and loses the `results/eval.csv` resolution
with them. Removing the keep that lets an already-named column ignore the row
beneath renames `Accuracy` to `Accuracy avg`, which canonicalises to nothing,
so a claim the tool could name becomes one it cannot. Neither mutation touches
the control table, and neither moves any other case in the corpus.

```console
adduce check corpus/synthetic/synthetic_mixed_header_row \
  --paper corpus/synthetic/synthetic_mixed_header_row -f json
```

to see the drafted claims.
