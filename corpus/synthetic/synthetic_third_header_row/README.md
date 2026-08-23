# synthetic_third_header_row

A header of three rows — a group row over a dataset row over a metric row —
and, in the same paper, the table shape that a careless reading of it would
destroy.

The first table is t5's Table 16 in miniature. `\multicolumn{3}{c}{GLUE}` spans
three columns above `Score`, `CoLA` and `STS-B`, and the metric — `Average`,
`MCC`, `SCC` — sits in a third row beneath those. A parse that composes at most two header rows reads the
third as data: it states no number, so it yields no cell and vanishes silently,
and every column below it keeps the *group* name. On the real table that is
2,277 cells labelled `GLUE`, `SuperGLUE`, `WMT` or nothing at all, which is
coarser than even the dataset names, and it is why t5 scored 0 of 15 with 13 of
its values already extracted. Read correctly this table states
`GLUE Score Average = 74.7`, `GLUE CoLA MCC = 53.8`, `GLUE STS-B SCC = 87.1` and
`SQuAD v1.1 F1 = 88.5`, which canonicalise to `average_score`, `matthews`,
`spearman` and `f1`.

The `Score`/`Average` column is carried by the rest of the table and is the
reason it is here. `Average` names no metric on its own, so the pair
`Score`/`Average` could never satisfy the header test by itself; what makes that
column readable is that *other* columns of the same row pair a dataset with a
metric, and the header test is asked of the row rather than the column. This is
exactly how t5's GLUE average is recovered, and it is why `average_score` is
registered on `score average` and not on a bare `average` — see
`tests/test_claims_extraction.py`, where the bare forms are pinned to `None`
because a canonicalising header pre-empts the caption fallback.

The second table is the control, and it is the more important half. Its `Prior`
row states `$83.1 \pm 0.4$`, which is not a number any more than a header is, so
a rule phrased as *absorb rows into the header while they state no number* would
swallow it and rename `MNLI` to `MNLI $83.1 \pm 0.4$`. Measured over the 34 dev
pairs that phrasing claims **136** tables against the correct rule's **5**: the
same shape appears in gpt-neox's uncertainty cells, BERT's `(Acc)` units row and
the hyperparameter tables of mae, lora and bit. What holds it to 5 is requiring
the *last* absorbed row to name a metric the row above it leaves unnamed. So
this table must read `Ours` as a row label and report `MNLI = 84.2` and
`QNLI = 91.0`, with `Prior` contributing nothing rather than becoming a column
name.

The two halves fail in opposite directions, which is the point of putting them
in one paper: a fix that is too timid loses the first table, and a fix that is
too eager loses the second.

**This case is exercised by the `--paper` byte-identity comparison and by
`tests/test_collectors_new.py`, not by `expectations.yaml`.** The LaTeX
collector does run there — `run_check` finds `paper/main.tex` inside the case
directory whether or not a paper path is passed — but no rule reads a table cell
or a drafted claim, so no fix to how a cell is read can move a verdict. The
expectations entry pins only that a paper whose cells reach no rule produces no
verdict rather than a wrong one; the entry for `synthetic_wrapped_table_header`
in `../expectations.yaml` states that caveat in full.

Verified by mutation in both halves: capping the header at two rows renames
`GLUE CoLA MCC` back to `GLUE` and drops all three canonical metrics, and
dropping the metric requirement from the search renames `MNLI` to
`MNLI $83.1 \pm 0.4$`.

Both byte-identity instruments are live on it, which is not something to assume
— they are blind in different directions. Against a tree capped at two header
rows, `bench/dev/manifest_identity.py` reports **1 of 30 cases moved**, this
one, on 4 of its 6 claims and on `column_label`, `metric`, `value`, `text`,
`confidence`, `resolution_method` and `produced_by`; the `--paper` JSON-report
comparison over the same two trees also reports **1 of 30**, this one. Every
other case is identical under both, so the case moves what it was built to move
and disturbs nothing else.

```console
adduce check corpus/synthetic/synthetic_third_header_row \
  --paper corpus/synthetic/synthetic_third_header_row -f json
```

to see the drafted claims.
