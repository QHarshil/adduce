# synthetic_wrapped_row_label

Every way a row's name can arrive wrapped in markup that prints nothing, in one
table, with the values it names beside it.

```latex
\multirow{2}{*}{Detectron} & Mask R-CNN & 37.8 & 34.2 \\
 & RetinaNet & 36.4 & 32.1 \\[0.7mm]
\midrule[0.3pt]
\parbox[b]{2cm}{SimpleDet} & Mask R-CNN & 37.1 & 33.7 \\
\multirow{2}{0em}{\rotatebox[origin=c]{90}{\makebox[0em]{\hspace{-0.2em}Ours}}} & ...
```

Read as the page prints it, the four rows are named `Detectron`, `RetinaNet`,
`SimpleDet` and `Ours`. Read by a cleanup that erases command names and braces
and keeps what was between them, they were named `2*Detectron`, `RetinaNet`,
`2cmSimpleDet` and `20em[0em]Ours`, and the row after the `\\[0.7mm]` break
opened with `[0.7mm]`, and the row after the trim-specified `\cmidrule`s opened
with `(r0.2cm)1-2 (l0.1cmr0.1cm)3-4`.

**This family cannot be dissolved the way a citation key is.** `\cite`,
`\hspace` and `\ref` are removed *with* their arguments, because the argument is
never printed. Here the **last** argument is the text and the ones before it are
layout, so removing the command with all its arguments deletes the label
outright. The leading arguments are dropped and the last is spliced in.

A row label is the clustering key and half the precision verdict key, so this is
not cosmetic. Measured across the 34 dev pairs the batch repairs **407** cells'
row labels and leaves **none** of this class behind: mmdetection 129
(`10*Mask R-CNN`), clip 88 — whose labels are three wrappers deep, exactly the
last row here — llama 51, mt-bench 44, albert 30 (`3*BERT`, `4*ALBERT`), swin
26, lora 22, stylegan2-ada 6, bert 5, deit 3, grounding-dino 3.

**What this case deliberately does not fix, and pins as such.** The second
Detectron row is named `RetinaNet`, not `Detectron`. A `\multirow` spans rows
and the rows it spans are separate rows of the parse, so the label reaches only
the first; the rows beneath it take their name from the next filled cell. That
is a real limitation and it is asserted here so that the day it is fixed, this
fixture is what says so. The related question — that a spanning label must *not*
also repeat across columns, the way `\multicolumn` does — is asserted in
`tests/test_collectors_new.py`.

Verified live, not assumed: against a tree with the wrapped-text family and the
row-break skip removed, `bench/dev/manifest_identity.py` reports **1 of 32 cases
moved**, this one, on `row_label` and `text` across 6 of its 8 claims, with
every other case identical.

**This case is exercised by the `--paper` byte-identity comparison and by
`tests/test_collectors_new.py`, not by `expectations.yaml`.** The LaTeX collector
does run — `run_check` finds `paper/main.tex` inside the case directory whether
or not a paper path is passed — but no rule reads a table cell, so no fix to how
a cell is read can move a verdict. The expectations entry pins only that a paper
whose cells reach no rule produces no verdict rather than a wrong one; the entry
for `synthetic_wrapped_table_header` in `../expectations.yaml` states that caveat
in full.
