# synthetic_wrapped_table_header

A results table whose metric is named only inside a `\multicolumn` wrapping a
`\rotatebox`:

```latex
Model & \multicolumn{2}{c}{\rotatebox[origin=rc]{270}{Accuracy}} & F1 \\
```

Both wrappers take their text as the last of several arguments. The generic
cell cleanup strips command names without regard to argument structure, so it
glued the arguments onto the text (`2c[origin=rc]270Accuracy`), which names no
metric. The span was also dropped, so the header row was one column shorter than
the body row and the columns were labelled positionally. The table drafted no
claims.

Read correctly, the header names `Accuracy` for the two columns it spans and `F1`
for the last, and the paper states `accuracy = 81.4`, `accuracy = 79.2` and
`f1 = 88.0`: three claims.

No rule reads a table cell, so `expectations.yaml` pins only that the paper
yields no wrong verdict. `tests/test_synthetic_claim_fixtures.py` asserts the
three claims directly.
