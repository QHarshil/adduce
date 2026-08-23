# synthetic_wrapped_table_header

Both shapes of the undissolved-wrapper defect in one table, as measured on real
papers.

The metric is named only inside `\rotatebox[origin=rc]{270}{Accuracy}` — how a
paper fits a name over a narrow column — and that sits inside a `\multicolumn`.
Stripping command names without regard to argument structure concatenates the
arguments onto the text, so the column arrives as `1c[origin=rc]270Accuracy`,
which names no metric and is dropped before anything sees it.

The `\multicolumn` also spans two columns. Dropping the span leaves the header
row one cell shorter than the body row, so `79.2` is read under `F1` and `88.0`
runs off the end of the header and is named positionally as `col3`.

Read correctly, the table states `Accuracy = 81.4`, `Accuracy = 79.2` and
`F1 = 88.0`, and `results/eval.csv` resolves the first at rounding level
(81.37).

**This case is exercised by the `--paper` byte-identity comparison, not by
`expectations.yaml`.** The LaTeX collector does run there:
`tests/test_synthetic_corpus.py` calls `run_check` with no paper path, and the
collector finds `paper/main.tex` inside the case directory either way, so the
paper is read and its cells are parsed. What no rule reads is a table cell or a
drafted claim, so no fix to how a cell is read can move a verdict. The
expectations entry therefore pins only that a paper whose cells reach no rule
produces no verdict rather than a wrong one, and states that caveat in full for
every paper-side case. Run

```console
adduce check corpus/synthetic/synthetic_wrapped_table_header \
  --paper corpus/synthetic/synthetic_wrapped_table_header -f json
```

to see the drafted claims.
