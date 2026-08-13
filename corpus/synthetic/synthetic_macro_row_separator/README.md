# synthetic_macro_row_separator

A paper that defines commands of its own, in the three shapes that decide
whether a table can be read at all.

`\tsep` is ELECTRA's row separator. Every row after the first header ends with
it rather than with `\\`, so a body split on `\\` alone joins the second header
row to the first body row. The joined row states numbers, so it is no longer
recognised as a header, the metric under each dataset is lost, and every column
past the join is named positionally. ELECTRA recovered 289 table cells this way,
36 of them under a positional or unusable header, and named none of its metrics.

`\ourmodel` is BERT's row label. Its own rows read `\bertlarge (Single)`, and a
cleanup that strips the command strips the model name with it, leaving
`(Single)` — which destroys the one signal on the page saying whose result the
row is, and states a claim text no reader can check against the paper.

`\tstrut` is the control, and it is a refusal rather than a recovery. It prints
nothing, and the cell cleanup already erases it by name; expanding it would put
`\rule{0pt}{2.6ex}` where the name was, which that cleanup cannot dissolve, so
`0pt2.6ex` would join the header beside it. MoCo's `\shline` is the same shape
and expanding it prefixed every row label in that paper with `1pt`. `\demph` is
the second control: it takes an argument, so its body is never substituted.

Read correctly, the paper states `SQuAD EM = 84.1`, `SQuAD F1 = 90.9`,
`MNLI EM = 80.5` and `MNLI F1 = 88.1` for `TinyNet_LARGE`, and the same four
columns for `Prior work`.

**This case is exercised by the `--paper` byte-identity comparison, not by
`expectations.yaml`.** Table cells reach the claims package and no rule reads
them, and `tests/test_synthetic_corpus.py` calls `run_check` without a paper
path, so the LaTeX collector never runs there. The expectations entry therefore
pins only that an unread paper produces no verdict.

It exists because that byte-identity check was otherwise **vacuous** for macro
expansion: no other synthetic paper defines a command at all, so suppressing the
expansion moved none of the sixteen targets. It moves this one. Verified by
mutation in each part — suppressing expansion leaves seven cells under the
positional headers `col5` to `col12`, every row label empty and the value `88.1`
lost outright, and removing the dimension refusal renames `MNLI EM` and
`MNLI F1` to `MNLI 0pt2.6ex EM` and `MNLI 0pt2.6ex F1`.

```console
adduce check corpus/synthetic/synthetic_macro_row_separator \
  --paper corpus/synthetic/synthetic_macro_row_separator -f json
```

to see the drafted claims.
