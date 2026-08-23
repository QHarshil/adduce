# synthetic_coincident_values

A paper that states one value three times as three different measurements, and
a fourth time as a genuine restatement of one of them.

De-duplicating claims on `(metric, value)` alone reads all four as one claim.
That is not a tidiness problem: two adjudicators found it independently from
opposite ends, and in both cases an own result became a claim attributed to a
competitor's row. BERT's own `(Ens.+TriviaQA)` dev F1 of 92.2 vanished into an
ELMo baseline of 92.2 printed in another table, and Barlow Twins' own AP50 of
82.6 vanished into SwAV's. A locator cannot separate them, because every cell
of one `tabular` records the line the environment opens on, so two cells of one
table are indistinguishable by location.

What separates them is that a candidate's row and column together name what was
measured. The first table here states `84.1` three times: for the authors' model
on the development split, for the authors' model on the test split, and for
prior work on the development split. One of those differs from the first by
column and one by row, and none of the three is a restatement of another. They
must remain three claims, each keeping its own row and its own locator.

The second table is the other half, and it is the half a fixture showing only
the split would miss. It repeats the full model's development-set exact match
from the table above, which is what an ablation table does, and that is one
number stated in two places rather than two claims. It must merge, and the
merged claim must keep both locations. A rule that never merged two labelled
cells would pass the first half of this fixture and fail here — cell identity
was measured as the alternative and rejected on exactly this shape, since Barlow
Twins prints `Baseline / Top-1 = 71.4` in two ablation tables and Whisper
repeats model-by-dataset rows in its appendix, 18 genuine restatements between
them that cell identity would have split.

Read correctly, the paper states `Dev EM = 84.1` for the authors' model at two
locations as one claim, `Test EM = 84.1` for the authors' model, `Dev EM = 84.1`
for prior work, `Test EM = 82.6` for prior work, and the ablation's remaining
four numbers: seven claims from eight cells.

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

It exists because that byte-identity check was otherwise **vacuous** for
clustering of any kind. The seventeen cases that preceded it produce twenty claim
candidates and not one multi-member cluster between them, so neither a rule that
never separates two measurements nor one that never merges them moves any of the
seventeen. `synthetic_rounding_match` does not fill that gap: it moves under an
`_agree`-always-false mutant through `reconcile.matching_results` resolving
`produced_by.log`, not through clustering. This is the corpus's first
multi-member cluster. Verified by mutation in both halves — never separating two
different measurements collapses `Ours: Test EM` and `Prior work: Dev EM` into
`Ours: Dev EM` and takes the paper from seven claims to five, and never merging
two labelled cells splits the restatement and takes it to eight. Neither
mutation moves any other case in the corpus.

One limit is worth stating rather than leaving to be discovered: no reporter
surfaces that a claim was stated in two places. `ClaimCluster.restated`
computes it and nothing reads it, so the merged half of this fixture is visible
in the output only as the absence of a second claim. That is enough for the
byte-identity comparison, and it is not enough for a reader.

```console
adduce check corpus/synthetic/synthetic_coincident_values \
  --paper corpus/synthetic/synthetic_coincident_values -f json
```

to see the drafted claims.
