# synthetic_reference_row_label

A paper whose ablation table is written the way a paper writes one that restates
its own earlier tables: a leading column of cross-references naming where each
row first appeared, a rule of a stated thickness above the header and between two
body rows, and a font size set on a header cell. None of that markup prints
anything a label should carry, and all of it was reaching one — and in the case of
the cross-reference, it was *displacing* the label entirely.

This is the second batch of the family `synthetic_markup_residue` opened. The cell
cleanup erases a command name and its braces but keeps what sits between them,
which is right for `\textbf{Ours}` and wrong for every command whose argument is
not content. Three more such commands, and one consequence that no amount of
dissolving fixes on its own:

- **`\ref{tab:baseline}` became the row's name.** What a cross-reference prints is
  a number assigned at typesetting time, which this collector cannot compute; what
  it *carries* is an internal key. t5 leads every body row of its Table 16 with
  one, naming the main-body table that row restates, so **2,277 of its cells were
  labelled `tab:baseline`, `tab:architectures_results` and eleven more keys of the
  same kind** — a seventh of the pair's cells, under labels no reader could act on.
- **`\Xhline{1.0pt}` prefixed what followed it.** `makecell`'s rule of a stated
  thickness was not recognised, so the thickness stood where the next row's first
  cell begins. The residue lands in a different place depending on what follows:
  ahead of a body row it prefixes that row's label, and **ConvNeXt labelled 14 rows
  `0.3 Swin-T` and `0.3 Swin-B^`** — the `0.3` of an `\Xhline{0.3\arrayrulewidth}`,
  a rule three-tenths of the default thickness read as part of a model's name.
  Ahead of a spanned sub-caption it joins the first header row, and composition
  then copies it onto every column beneath, which is why **Swin carried it on 24
  cells** rather than one row's worth: `1.0pt (a) Various frameworks AP^box`.
- **`\fontsize{7.5pt}{1em}` left both of its arguments behind.** A size and the
  baseline skip that goes with it, neither printed. Removing one would have left
  the other exactly where both were, so the guard is written with its arity:
  measured, **MoCo headed 43 cells `7.5pt1em COCO keypoint detection` and
  `7pt1em accuracy (\%)`**.
- **Dissolving the reference is not enough, and on its own it is worse.** The row
  label was the first cell unconditionally, so emptying that cell renamed 2,277 t5
  cells to the empty string. That is the one condition `claims.cluster` cannot
  survive: `_other_measurement` separates measurements by row and column and
  cannot see which table — or which row — a cell came from, so identically named
  cells whose values round together become one claim. **Measured, the empty name
  alone collapsed t5's 2,339 claims to 1,689**, destroying 650 by merging rows that
  state different things. The row is therefore named from the next cell along
  instead, which is the `Experiment` column the paper prints, and which CLIP needs
  too: **1,775 of its cells were labelled with the empty string** before this.

The search past an empty cell is bounded in both directions, and the last two rows
of `paper/main.tex` are a control for each. It never overrides a *filled* first cell, even a
numeric one — a number is a perfectly good name for a row, and t5's Table 7 labels
its rows by span length, where `10` is what one of them is called; letting the
search reach past a filled cell cost bert 55 of its row labels and t5 that `10`.
And it stops at the first cell stating a number, because that cell is already
extracted as the row's first value, so reading it as the name as well would have
one cell play both parts. An unnamed row is the honest answer there.

Without the fixes this case yields the same eight cells under labels a reviewer
cannot use:

```text
'tab:baseline'      '1.0pt (a) Ablations restated 7.5pt1em Accuracy'  88.1
'0.3 tab:scaling'   '1.0pt (a) Ablations restated 7.5pt1em Accuracy'  91.4
```

With them, `'Baseline average'` and `'Ours'` under `(a) Ablations restated
Accuracy`. Every guard is load-bearing here, which is the point of the fixture:
the second `\Xhline` sits between two body rows precisely so that suppressing that
guard leaves the row's first cell *non-empty*, whereupon the search never fires and
the label reads `0.3` instead of `Ours`.

**No rule verdict moves on this case, and that is stated rather than worked
around.** Table cells reach only the claims package, and no rule reads a drafted
claim; measured, all 28 findings this case yields are byte-identical before and
after the change, as are `synthetic_markup_residue`'s. That 28 counts findings,
not cases — the case counts below are a different quantity that happens to sit
near it. What `expectations.yaml`
pins here is the same thing it pins for that case: a paper whose table is now read
correctly still yields no verdict about anything it did not measure — the prose
accuracy reconciles with `results/eval.csv` and no phantom hyperparameter appears.

The assertions that bite are elsewhere. `tests/test_collectors_new.py` pins each
shape above on its own, both controls, and this fixture's eight cells as a whole,
and each of the eight guards is proven live by mutation: suppressing any one fails
its own named test and nothing else. Both byte-identity instruments see this case
and no other — the manifest harness reports it moved on `column_label`,
`row_label` and `text` on 8 of 8 claims, and the JSON report reports it at 18,222
bytes to 18,150, with every pre-existing case byte-identical on both. Both were
measured against themselves first and reported every case identical, which is
what shows `/repository/commit` and `/repository/root` are both normalised. Both
are live for this class: disabling metric canonicalisation outright moves 15 cases
on the manifest harness and 13 on the JSON report, measured at the 28 cases the
corpus held when this was added.

**Recall is unchanged at 119/296 over the twenty dev pairs, with no pair falling.**
Cross-table cluster merges go from 92 to 106: nine spurious ones disappear from
CLIP and three from albert, both of which had merged cells across tables under the
empty label, and 24 appear in t5 where Table 16's restatements now carry the same
row and column names as the main-body rows they restate — which is one number
stated twice, and one claim. The 25th is Swin's, where sub-tables (a) and (b) both
report Swin-T under Cascade Mask R-CNN at 50.5/69.3/54.9, 86M, 745G and 15.3 FPS;
that merge is why Swin's claim count falls by one, and it is correct.

```console
adduce check corpus/synthetic/synthetic_reference_row_label \
  --paper corpus/synthetic/synthetic_reference_row_label -f json
```

to see the eight claim headlines, and

```console
adduce manifest corpus/synthetic/synthetic_reference_row_label \
  --paper corpus/synthetic/synthetic_reference_row_label
```

for the cell labels and the resolution method the JSON report drops.
