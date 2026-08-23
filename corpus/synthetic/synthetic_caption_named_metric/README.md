# synthetic_caption_named_metric

Both shapes of the metric-named-outside-its-column defect in one paper, as
measured on real ones.

The first table is Whisper's shape. Its columns are *datasets* — `LibriSpeech`,
`CORAAL` — and the metric is stated once, in the caption. Read from the header
alone, every cell in that table is named after a corpus, so no cell can ever
match a claim about word error rate. Whisper extracted 2,098 such cells and
matched none of its fifteen labelled numbers while every one of those values was
already collected: the gap was naming, not collection.

The third column of that same table is the control. `Accuracy` names a metric
itself, and the caption says WER. A caption that overrode it would rename a
column the paper had already named — so the header wins, and the fixture shows
it winning.

The second table is Mamba's shape. Its first header row carries dataset names
and the metric sits in a *second* header row beneath them, which a parse that
reads `rows[0]` and treats everything after it as data reports as `SQuAD = 88.5`
rather than `SQuAD F1 = 88.5`. Its caption is deliberately placed *after* the
`tabular` and deliberately names no metric, so it also pins that a caption is
found on either side of the table it belongs to and that a caption naming
nothing supplies nothing.

Read correctly, the paper states `WER = 3.4` and `WER = 12.8`, `Accuracy = 91`,
`F1 = 88.5` and `Accuracy = 84.2`, and `results/eval.csv` resolves the first at
rounding level (3.42).

**This case is exercised by the `--paper` byte-identity comparison, not by
`expectations.yaml`.** The LaTeX collector does run there:
`tests/test_synthetic_corpus.py` calls `run_check` with no paper path, and the
collector finds `paper/main.tex` inside the case directory either way, so the
paper is read and its cells are parsed. What no rule reads is a table cell or a
drafted claim, so no fix to how a cell is read can move a verdict. The
expectations entry therefore pins only that a paper whose cells reach no rule
produces no verdict rather than a wrong one; the entry for
`synthetic_wrapped_table_header` in `../expectations.yaml` states the caveat in
full.

It exists because that byte-identity check was otherwise **vacuous** for this
behaviour: no other synthetic paper contains a `\caption` or a second header
row, so neutering the caption rule moved none of the fifteen targets. It moves
this one. Verified by mutation in both halves — suppressing the caption lookup
renames `LibriSpeech` and `CORAAL` back to themselves and loses the `eval.csv`
resolution; suppressing the second-header composition renames `SQuAD F1` back to
`SQuAD`.

```console
adduce check corpus/synthetic/synthetic_caption_named_metric \
  --paper corpus/synthetic/synthetic_caption_named_metric -f json
```

to see the drafted claims.
