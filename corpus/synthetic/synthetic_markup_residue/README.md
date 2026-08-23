# synthetic_markup_residue

A paper whose results table is written the way real papers write one: rules with
trim specifications, a shaded row, citations beside the baselines, negative skips
tightening the columns, and a header holding an escaped ampersand. None of that
markup prints anything a label should carry, and all of it was reaching one.

The cell cleanup erases a command name and its braces but keeps what sits between
them. That is right for `\textbf{Ours}` and wrong for every command whose argument
is not content, so each of the following survived into a row or column label —
which is what a reviewer reads, what the precision verdict key is now built from,
and, for a column, what decides whether the cell is read as a claim at all.

- **`\cite{...}` left its key glued on.** `Swin-T~\cite{liu2021swin}` reached a
  reader as `Swin-T~liu2021swin`, where nothing can tell a bibliography key from
  part of a model's name. ConvNeXt shipped
  `RegNetY-16G~Radosavovic2020designing`. The tie belongs to the citation idiom
  and goes with it.
- **`\cmidrule(lr){2-3}` labelled the row beneath it.** Only `\cline` was
  recognised, so booktabs' spelling left `(lr)2-3(lr)4-5` as BarlowTwins' row
  label, and t5 carried the residue on 2,440 cells.
- **`\rowcolor[gray]{.95}` prefixed every label under it** with `[gray].95`, on 99
  of ConvNeXt's cells. DINO writes `\rowcolor{Light}`, so 76 of its rows were
  labelled with a colour name instead of a model.
- **`\hspace{-0.3em}` was invisible to every existing guard** — no brace, no
  backslash, no `=`, no leading digit — so `-0.3emGender-0.3em` passed as a
  plausible metric name and headed 44 of CLIP's cells. This is the costly one:
  CLIP wraps its *values* the same way, and `-0.9em91.4-0.4em` states no number
  the parser can find, so **2,025 result cells were dropped outright** — the whole
  of CLIP's two largest appendix tables, read as 5 cells where there are 2,030.
- **`\&` was split as a column separator.** It prints an ampersand and separates
  nothing. DINO's DAVIS header `$ (\mathcal{J}$\&$\mathcal{F})_m$` became two
  cells, leaving seven headers over six columns, so every column after it was
  named by the wrong header: 18 cells reported under `F)_m` and `J_m`, and the
  header that should have carried them was never seen.
- **`\,` and source line breaks reached labels too.** The generic cleanup needs a
  letter after the backslash, so the thin space ConvNeXt writes ahead of every
  model name survived on 115 cells; and a label spanning source lines kept its
  newlines and indentation, which also defeats `canonical_metric`, whose
  whole-name lookup is a dictionary hit.

This case plants all of them in one five-column table. Without the fixes it
yields **six cells under three wrong column names**; with them, **eight cells**
whose labels are what the page prints. What a reviewer is shown moves from

```text
paper/main.tex:13  ·  "(lr)2-3(lr)4-5
Swin-T~liu2021swin: F)_m = 60.2"
```

to `paper/main.tex:13  ·  "Swin-T: J_m = 60.2"` — and 60.2 is in the `J_m` column,
so the old headline named the wrong metric as well as reading unintelligibly.

`Swin-T` is the second control. Its row is cited, so it must still be marked as
prior work: the attribution flag is read from the raw row and the label is cleaned
from a copy, precisely so that dissolving a citation cannot promote a quoted
baseline to a confident own result. `Accuracy` is the third — a header that
canonicalises either way, once the skip around it is gone.

**No rule verdict moves on this case, and that is stated rather than worked
around.** Table cells reach only the claims package, and no rule reads a drafted
claim; measured, all 28 findings this case yields are byte-identical either way.
That 28 counts findings, not cases — the case counts below are a different
quantity. What `expectations.yaml` pins here is that a paper whose table is now read
correctly still yields no verdict about anything it did not measure: the prose
accuracy reconciles with `results/eval.csv` and no phantom hyperparameter appears.

The assertions that bite are elsewhere, and both are non-vacuous by measurement.
`tests/test_collectors_new.py` pins each shape above on its own plus this
fixture's eight cells, and each is proven live by mutation: suppressing any one
guard fails its own named test and the fixture test, and nothing else. And the two
byte-identity instruments both see it — `bench/dev/manifest_identity.py` reports
**two cases moved** (this case at claims 5 → 8, and
`synthetic_quoted_baseline_rows` at `row_label`/`text` on 4 of its 10 claims) and
the default JSON report **four**, adding the two typesetting-state cases whose
faults are hyperparameters rather than claims. Both instruments were measured
against themselves first and reported every case identical, which is what shows
`/repository/commit` and `/repository/root` are both normalised. Both are live for
this class: disabling canonicalisation outright moves 14 cases on the manifest
harness and 12 on the JSON report, measured at the 27 cases the corpus held when
this was added.

**Recall is unchanged at 119/296 over the twenty dev pairs, with no pair
falling**, and the row and column labels of four adjudicated pairs move, which is
the re-staling this batch was scheduled to do once.

```console
adduce check corpus/synthetic/synthetic_markup_residue \
  --paper corpus/synthetic/synthetic_markup_residue -f json
```

to see the eight claim headlines, and

```console
adduce manifest corpus/synthetic/synthetic_markup_residue \
  --paper corpus/synthetic/synthetic_markup_residue
```

for the cell labels and the resolution method the JSON report drops.
