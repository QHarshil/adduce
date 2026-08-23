# bench/dev — the development set

The set claim extraction is measured on during iteration. It is deliberately **not** the
evaluation set: the ten claim-carrying repositories behind the r6/r7 truth are read for the
final effectiveness report and never during development, so iterating against them would
spend the holdout. Nothing here may name one of those repositories, and `fetch.py` refuses a
holdout URL rather than trusting the roster to be right.

`bench/` is outside the preregistration hash, so this tree can evolve continuously without an
amendment.

## What a pair is

One official implementation repository, pinned at a commit, paired with the arXiv **e-print
source** of the paper it implements. `pairs.csv` records the pin — repository URL and commit,
arXiv identifier and version, title, framework, licence. It does not record the content.

**Third-party content is fetched, never vendored.** `fetch.py` populates a gitignored
`bench/dev/pairs/` tree and writes a manifest recording what was actually retrieved: the
resolved commit, the digest and size of the source tarball, the digest of the PDF, the arXiv
version served, and a status per pair. Failures are recorded, never silently dropped. This is
the discipline `corpus/scripts/clone_repos.py` already applies to the validation corpus.

Repositories are pinned at their current head rather than at a commit contemporaneous with the
paper. That is deliberate: a repository that has drifted years past the paper it implements is
the ordinary case a reader meets, and staleness is one of the four relations the system exists
to detect. It has no bearing on extraction recall, which is measured on the paper alone.

## Ground truth

### Labelled from the rendered PDF, never from the LaTeX source

This is the rule the metric rests on. Ground truth transcribed from `.tex` is written by
someone reading the source the same way the extractor reads it, so a number the extractor
cannot see — inside `\multicolumn`, behind `\resizebox`, defined by a macro, split across an
`\input` — is equally invisible to the person recording the truth. Recall would then measure
the extractor against its own assumptions and could not fall below a ceiling set by the
labeller's own parse. From the PDF, the truth is what a reader sees on the page, which is what
a claim is.

### The label unit is one reported number

```json
{
  "id": "L7",
  "metric": "top-1 accuracy",
  "value": 84.5,
  "units": "%",
  "dataset": "ImageNet-1K",
  "split": "val",
  "role": "result",
  "is_own_result": true,
  "confident": true,
  "location": {"kind": "table", "label": "Table 3", "page": 7, "row": "Swin-B", "column": "top-1"},
  "notes": ""
}
```

These objects go in the `labels` array of the file described under "The denominator is a
sampled frame" below.

`role` is `result`, `hyperparameter` or `dataset_statistic`. A paper is full of numbers that
are not claims about outcomes — epoch counts, learning rates, corpus sizes — and only `result`
enters the recall denominator. Hyperparameters are a different claim type, already owned by the
drift rules. The field is named `role` because `manifest.Claim.kind` already exists with a
different vocabulary.

`is_own_result` separates the paper's own measurement from a baseline quoted from prior work.
A results table holds both, and a baseline number has no producer in the repository, so
reporting it as unsupported would be a confident false positive — the failure mode that costs
the most trust. **The recall denominator is `role == "result" AND is_own_result`.**

### The denominator is a sampled frame, and the frame is recorded

A single results table can hold several hundred cells. Twenty-plus papers cannot be
exhaustively hand-transcribed without transcription error corrupting the very metric the labels
exist to establish. So labelling proceeds in two steps:

1. **Enumerate the frame.** Every result-bearing table and paragraph, with its count of
   own-result cells: `{"Table 3": 48, "Table 4": 12, "abstract": 2}`. Cheap, objective, and
   complete — no cell is excluded by judgement at this stage.
2. **Label a seeded uniform sample** drawn from that frame, recording the seed and the target
   size. Where a frame is small enough to label completely, label it completely and record
   `sampled: false`.

The file `recall.py` actually parses is shaped like this — `sampled` and `sampling_seed` are
**top level**, and `frame` is a **flat map of location to own-result count**:

```json
{
  "schema": "adduce-dev-labels/1",
  "pair_id": "swin",
  "arxiv_id": "2103.14030",
  "arxiv_version": "2",
  "labelled_from": "pdf",
  "labeller_passes": 1,
  "sampled": true,
  "sampling_seed": 20260810,
  "target_sample_size": 25,
  "frame": {"Table 1": 12, "Table 3": 48, "abstract": 2},
  "labels": [ ... ]
}
```

Any other key is carried without complaint, so richer per-location detail (page numbers, for
instance) can live alongside in a `frame_locations` array. Validate a finished file by loading
it through `recall.load_label_frame`, not with `json.load` — well-formed JSON in the wrong
shape parses fine and fails only later, at measurement time.

Recall is then an unbiased estimate over the paper's reported numbers against a stated sampling
frame, rather than an exact figure over a subset chosen by convenience. `sample_findings.py`
applies the same discipline to findings.

### Rendering a paper for labelling

`paper_text.py` produces both permitted surfaces. Both are the *rendered* paper, so
both are allowed; the LaTeX source is not.

```console
python bench/dev/paper_text.py swin --out /tmp/labelling            # page text
python bench/dev/paper_text.py swin --out /tmp/labelling --pages 6,7  # plus two page images
```

Text is cheap and is enough to enumerate the frame and to read prose numbers and
figure captions. It loses column alignment, so attributing a value to the right row
and column needs the page image — render only the pages carrying cells actually being
transcribed. Reading every page of every paper as an image is what made three earlier
labelling attempts stall without producing anything.

Extracted text renders "fi" as the ligature "ﬁ". Digits are unaffected.

`page.find_tables()` was evaluated for structured cell extraction and rejected: on
these papers it returns mostly empty and merged cells, which would mis-attribute
values silently.

### Labelling rules

- Transcribe the value exactly as printed, including trailing zeros; record units separately.
- Record the metric as printed, not canonicalised. Canonicalisation is the extractor's job and
  is applied to both sides at match time.
- A cell attributed to a citation is a baseline. A cell reporting the paper's own method is an
  own result, and so is the authors' own reimplementation of prior work, because that number
  was produced by this repository.
- **Ownership is per cell, not per row.** A caption routinely claims one column for the authors
  while the rest of the row stays quoted: ConvNeXt states it measured inference throughput and
  FPS itself for *every* row shown, competitors included, while the accuracy beside it remains
  a citation. Reading ownership off the row name alone undercounted that paper's frame by 207
  against 245. Captions carry this and the text dump often does not, so it is worth a page
  image on any table whose caption mentions how a number was obtained.
- **A delta is a cell only when no absolute value is printed beside it.** Papers restate a
  result and its improvement in one breath ("86.7% (4.6% absolute improvement)"); counting both
  double-counts one measurement. Where only the delta is printed ("4.5% average accuracy
  improvement"), it is the reported number and counts.
- **One token holding two metrics is two cells** — `F1/EM`, `GFLOPS/FPS`. The label unit is one
  reported number, and the two are separately claimed.
- A range rather than a point value ("adds 1-2 AP") is not a cell: `value` is a single number
  and choosing an endpoint would misrepresent the page.
- Speed, throughput and memory figures presented as outcomes are results, with the metric named
  as printed.
- Where ownership or role is genuinely unclear, record the reading in `notes` and set
  `confident: false`. Unconfident labels are retained but excluded from the denominator, so an
  ambiguous cell never silently becomes a miss.

### Stated limitations

Labels are produced in a single pass by one labeller, so no inter-rater agreement estimate
exists for this set and none is reported. That is acceptable for a development set and would
not be for the evaluation set, where `claim_review_metrics.py` already computes Cohen's kappa
over independent reviewers.

**The draws are not independent across pairs.** One seed is shared by the whole set, and
`random.sample` on a small `k` draws `_randbelow(N)`, which consumes
`getrandbits(N.bit_length())` and rejects values at or above `N`. Two frames of the same bit
length whose draws all fall below the smaller `N` therefore consume the same bits and accept
the same values. Measured over the twenty labelled pairs, there are **eleven distinct draws**:
`bert`, `bit` and `electra` share one, as do `blip`, `dino` and `moco`, and so do `clip` and
`t5` despite frames of 2,441 and 2,924.

Each pair's sample is still uniform over its own frame, so every per-pair recall figure is an
unbiased estimate and none of them is affected. What the correlation costs is the pooled
figure's precision: the same reading-order positions are sampled in every paper of similar
size, so if position correlates with content — early indices are abstracts and headline
tables, late ones appendices — the pooled estimate carries that structure and its effective
sample size is below the nominal one. A pooled confidence interval computed as though the
draws were independent would be too narrow, and none is reported.

Seeding per pair rather than per set removes this, and is what a rebuilt set should do. It is
not applied retroactively because re-drawing discards the labelling of every pair already
done, which is a real cost against an effect that biases no reported number.

## Measuring

`recall.py` runs the same path `check` runs — repository scan, evidence collection, LaTeX
collection over the paper directory, then the drafted claims — so it measures the shipped
extractor rather than a reimplementation of it. `--src` selects the source tree, which is what
allows a historical extractor to be measured retroactively against labels written long after
it: the LaTeX-prose-only baseline lives at commit `6f00c8b` and is reachable with
`git archive`.

An extracted claim matches a label only when **both** the value corresponds numerically and the
metric corresponds after canonicalisation. Value-only agreement is reported as a separate
diagnostic rather than counted, because where every candidate names the claim's metric,
matching on value alone reaches the right answer for the wrong reason.

### Recall and precision need separate evidence

They run in opposite directions, and one sampled label set cannot serve both.

Recall runs from the paper to the extraction: its frame is the paper's reported numbers, which
is what the labels hold. Precision runs from the extraction to the paper — *of the claims the
system produced, how many are real reported own-results?* — and that question is answered by
adjudicating each extraction, not by hoping it fell inside the labelled sample. Under sampling
most extractions land outside it, so precision derived from labels is largely undefined, and
"zero high-confidence false positives" is a precision statement that cannot be left undefined.

It is no longer undefined. `compute_precision` reports `high_confidence_false_positives`: the
adjudicated extractions that are not the paper's own result — excluding the two classes already
outside the precision denominator — and that were extracted at confidence `1.0`. Confidence is
not recorded in the verification file, so it is joined from the live extraction on
`(metric, value, where, row_label, column_label)`, the same key staleness uses; a verdict that
cannot be joined unambiguously is counted separately as `unjoined_false_positives` rather than
guessed at.
**Measured over the four adjudicated pairs: 96 of 310 false positives were extracted at
confidence 1.0, so the criterion is met by no pair.** Precision is 441/751 = 58.7 % pooled —
detr 104/138, convnext 186/297, bert 106/180, barlowtwins 45/136 — with `unclear` 0 across all
four. Note `adjudicated` already excludes `unclear` and `in_repo_not_paper`, which is why bert's
denominator is 180 against 186 verdicts.

The per-pair spread is the informative part, because it shows the reach of the baseline
demotion rather than a difference in extraction quality: **bert carries 2 where it once carried
25**, while barlowtwins holds 42 and convnext 47. Neither of those papers marks a quoted row
with a citation or a full-width section header, so the signal the demotion reads is simply
absent from them. Deciding whether a printed number is this artifact's own result is evidence-side
work, and the residue is the measure of how much of it the markup cannot answer.

### The matching key is `(metric, value, where, row_label, column_label)`

An adjudication describes one extraction, so a verdict is matched to the extraction it
adjudicates rather than counted against a key. `where` is load-bearing only because it is
handled carefully in two ways.

**Normalised.** The verification files and the extractions are rooted differently, and by a
depth that varies within one pair: a verdict records `src/main.tex:449` for a paper measured
here from `src` itself, while a repository README keeps `object_detection/README.md:12` on both
sides. So the root is recovered per file — a path resolves to the single extraction path that
is a `/`-boundary suffix of it, or that it is a suffix of. Measured over 674 verdicts on the
four adjudicated pairs: the raw locator resolves 26, the basename 660 but collapsing convnext's
`object_detection/README.md:18` and `semantic_segmentation/README.md:18` onto one key, dropping
the first path component 601 with the same collapse, and this form 660 with no two locators
sharing a key.

**Optional.** Two locators for one number are routine, and which of them survives clustering
moves with extractor changes that leave every number and every verdict untouched. A locator
that reconciles with no live extraction therefore falls back to `(metric, value)` rather than
dropping the match, and the fallbacks are counted and reported: `location_fallbacks` in the
coverage block, and `[no locator: N]` beside the rate in `measure`'s output. After the four
pairs were re-adjudicated that is convnext 4 and detr 2, barlowtwins and bert 0.

What the stronger key buys is that a repeated `(metric, value)` stays decidable. Extractions
are unique on `(metric, value)` today only because `claims/cluster.py` de-duplicates globally
on exactly that key; the moment that is repaired, a multiset difference over it stops naming
*which* row is stale, and the four adjudicated pairs have no way back to correspondence.

**The locator alone cannot separate two cells of one table.** Every cell of a `tabular`
records the line the *environment* opens on, so all of a table's cells share one locator
exactly. Two such collisions are verified and real: bert prints `88.5` as both R.M. Reader's
test F1 and BERT-BASE's dev F1, and convnext ties `15.01` GFLOPs between the cited ResNet-200
and its own enhanced recipe. Both were found while auditing whether the baseline demotion had
demoted a real own result, and the audit had to be done by hand because the key could not
distinguish the members. The row and column labels do distinguish them: when the
key was strengthened the old key collided 18 times within bert's 186 extractions and 23 times
within convnext's 302, and the key with the labels collided on neither. That counts extractions
in excess of one per key; the same data gives 32 and 39 for extractions *involved* in a
collision, and 14 and 16 for the number of colliding groups.

`manifest.Claim` therefore carries `row_label` and `column_label`, and the matcher narrows on
them **within** the group the locator selects. A verdict records them as `row_label` and
`column_label` beside `where` in its `extraction` object. They are optional on the verdict
side, and most verdicts do not record them: the four re-adjudicated files carry labels on
**114 of 757** verdicts — barlowtwins 15, bert 26, convnext 61, detr 12 — because the rest
predate the field. A verdict recording labels is matched only to an extraction agreeing on
them, with case and runs of whitespace flattened and nothing else. A verdict recording one
label is narrowed by that one, which is what a transposed table's verdict supplies. A verdict
recording neither is matched exactly as it was before, counted as `label_fallbacks` in the
coverage block and `[no labels: N]` beside the rate: barlowtwins 121, bert 160, convnext 236,
detr 126.

**A verdict whose labels match no extraction is matched on the locator alone rather than left
stale, and the degradation is counted** as `label_degradations` and `[labels dropped: N]`.
Today it is 0 on all four pairs.

The narrowing is still part of the key rather than a hint, and two properties are what make
degrading safe. Labels are dropped only *after* every verdict has been offered its narrowed
pool, so a degraded verdict can never take a cell that some verdict names exactly. And the
count is the reader's signal: where two free cells share a locator, a degraded verdict's
assignment between them is arbitrary, and a non-zero `label_degradations` is how that becomes
visible rather than silent.

Refusing instead — leaving such a verdict stale — was the earlier rule, and it was rejected
for the same reason the locator degrades rather than dropping. A label changes whenever the
parse changes what it reads from a cell, and cleaning typesetting residue out of row labels
moved hundreds of them in one pass. Under refusal every such cleanup would strand verdicts
whose number and whose human judgement never changed, which would leave the labels making the
instrument more brittle than it was before they existed.

So each pair carries a second, independent artifact, `verifications/<id>.json`, recording one
verdict per extraction: `real_own_result`, `baseline`, `hyperparameter`, `not_in_paper`,
`in_repo_not_paper` or `unclear`. Precision is computed from that file alone. The
label-derived false-positive count remains as a diagnostic over the recall frame and is never
presented as precision.

Two verdicts are excluded from the precision denominator and reported alongside it. `unclear`,
because it was not decided. And `in_repo_not_paper` — a claim the *repository* states that the
paper does not, typically a results table in a README. That is not a fabrication: the artifact
really does assert the number, and surfacing the claims an artifact makes is the system's job.
Nor is it a hit against a paper-scoped adjudication. Folding those into `not_in_paper` reported
bert's precision as 20/36 when it is 20/30.

Recall and precision therefore have independent availability: a pair may carry one and not the
other. A pair whose clone, paper, label file or verification file is absent is reported
`unavailable` for the affected metric, with the reason. It is never skipped silently and never
contributes a zero to an average.

## Regression cover: the all-pair inventory

Pooled recall is not a sufficient gate on an extractor change, and the reason is structural
rather than a matter of degree. Recall is defined over the labelled pairs, and the roster holds
**34** pairs against **20** label files — so 14 papers, 41 % of the roster, contribute nothing to
it. A change can destroy one of those papers completely and every gate will pass.

That is not hypothetical. A change that stripped LaTeX command definitions took
**latent-diffusion from 624 table cells to 0** and **stylegan2-ada from 66 to 0**, deleting real
reported numbers, because both papers wrap a whole float in a macro and invoke it in the body.
Pooled recall did not move, no test failed, and it was caught by a human reading the diff after
eight consecutive extractor changes had used recall as their safety criterion.

`inventory` is the cheap gate that sees it. It records `table_cells`,
`hyperparameter_values`, `metric_values`, `claims` and `numeric_claims` for **every** pair that
has a paper, labelled or not, and `compare-inventory` reports every pair whose counts moved in
either direction:

```console
python -B bench/dev/recall.py inventory --output before.json
# make the extractor change
python -B bench/dev/recall.py inventory --output after.json
python -B bench/dev/recall.py compare-inventory --before before.json --after after.json
```

It exits non-zero when anything moved, so it works as a gate rather than only a report. Run
against a tree with that defect reintroduced it prints
`latent-diffusion table_cells 624 -> 0, claims 511 -> 0` and `32 unchanged, 2 moved`.

A pair whose clone or paper is missing is reported unavailable with the reason, and its counts
are absent rather than zero — the same discipline `measure` applies, and the distinction matters
here more than anywhere, because a zero is exactly the signal the gate exists to catch.

**One value a reviewer has to know: `hf-transformers` reads 0 table cells in a healthy tree**
(with 4 claims). It is the one pair whose most sensitive signal is already floored, so 0 is its
normal reading there and not the next `624 -> 0`.

**The two prose-value counts were added after the gate missed a change, and the miss is worth
reading before trusting any other count here.** A hyperparameter never becomes a claim, and a
prose metric value that clusters into an existing claim moves no claim count either — so with
cells and claims alone, a change that deletes every hyperparameter a paper states is completely
invisible. That happened: the guard that stops a fraction's denominator being read as a batch
size was first written to search the whole window rather than the gap between keyword and number,
and it deleted real values from six papers — BERT's `Batch size}: 16` and `Learning rate (Adam)}:
5e-5`, BiT's `learning rate:} 0.003` and `momentum:} 0.9`, convnext's and MAE's `beta_2{=}0.9`,
simsiam's `acc. (\%)} & 68.1`. This gate read `32 unchanged, 2 moved`, the test suite passed,
ruff and mypy passed, and pooled recall did not move. It was caught by listing every candidate
the rule would refuse and reading them. **With `hyperparameter_values` and `metric_values`
recorded, the gate sees it.**

Current baseline over the 34 pairs: **19,135 cells, 1,307 hyperparameters, 326 prose metrics,
16,582 claims**, 34 available and 0 unavailable.

Two limits still stand. The inventory counts things, so it cannot see a change that alters
*which* number a cell yields without altering how many — for that the byte-identity harnesses
below are the instrument. And extraction over the roster is minutes of subprocess work, which is
why the two arms are separate artifacts rather than one run: the "before" for an extractor change
is the working tree as it stood, which is not a second source tree that can be pointed at.

An artifact written before a count existed reports `null` for it rather than zero, and
`compare-inventory` reads that as *not measured* rather than as a fall — the same discipline the
whole record rests on.

## Regression cover: `manifest_identity.py`

Recall and precision measure how good extraction is. Neither says whether a change *moved*
anything, and the synthetic corpus is where that question is asked. `manifest_identity.py`
answers it at the manifest level:

```console
python -B bench/dev/manifest_identity.py compare --before <tree>/src --after <tree>/src
```

For every case directory under `corpus/synthetic` it drafts that case's manifest with `--paper`
at the case root, digests the bytes `adduce manifest` would write, and reports each case
`identical` or `moved` plus a total. A moved case is named down to the claim fields
that differ, so a confidence-only move is distinguishable from a changed extraction. Each arm
reports the directory it imported `adduce` from, and the report says outright when the two
agree — two arms that resolved one tree measure nothing however clean the result looks.

**What it covers that the JSON-report check does not.** The default JSON report carries a
claim's metric, value, location and trail and carries **neither its confidence nor its
resolution method**, so it is blind to any change that moves only how confidently a number was
read. That is not a small class: the baseline demotion moved 157 extractions on the dev set and
every synthetic case's report was identical across it. Measured on two trees differing only in
whether a cell attributed to prior work keeps full confidence — the source tree against a copy
with that one condition removed — this harness reports **two cases moved**:
`synthetic_markup_residue` on 2 of its 8 claims and `synthetic_quoted_baseline_rows` on 6 of its
10, both on `confidence` and `resolution_method` alone, with every other case identical. The
JSON-report check over the same two trees reports **no case moved at all**. The report check is
not broken: with second-header composition suppressed it moves four cases. It cannot see this
class at all, which is the whole reason this harness exists.

Beyond those two fields the digest covers the whole drafted manifest — the claim set, each
claim's metric, value, locator, text and cell labels, and the paper, environment, dataset,
remote and smoke sections — so an extraction change is visible here too. What it does not do is
say a confidence is *correct*. It reports movement, and the reader decides.

**Its reach over claims is whatever the corpus drafts**, which makes it a property of the
corpus rather than of the harness. A case that drafts no claim — the drift, seed, secret and
dependency fixtures — can move here only through the paper, dataset, environment, remote or
smoke sections, and is still measured for exactly that reason. The report counts cases and
claims on every run, so read the live figures off it rather than off a fraction written down
here, which goes stale the next time a case is added. Measured over the 29 case directories the
corpus held when this was written: 18 draft at least one claim and 87 in total,
`synthetic_quoted_baseline_rows` holding 10 and `synthetic_metric_vocabulary` and
`synthetic_mixed_header_row` 9 each. Those are **manifest-level** counts, so they include the
one claim `synthetic_hydra_authority` carries in its author-written manifest and extraction
never produces; `extract_claims` alone accounts for 17 cases and 86 clusters.

**Two cases carry the demotion signal, and one of them carries only half of it.**
`synthetic_quoted_baseline_rows` has both halves — a citation in a row label and a prior-work
section row — and its README records which cells each half accounts for.
`synthetic_markup_residue` leads a row with `Swin-T~\cite{liu2021swin}` and carries the citation
half alone: four of its cells are attributed to prior work, and two of its claims are demoted by
that attribution, the other two being read at reduced confidence anyway because their column
names no metric this build knows. No other case carries either half, so cover for this change
class is thin rather than absent, and a demotion refinement reaching markup no case contains
would still measure nothing moved.

**It never dirties the repository.** `adduce manifest` writes `.adduce/manifest.yaml` under the
path it is given and `corpus/synthetic` is tracked, so each case is copied out of the tree and
drafted in a temporary directory. The one case that ships an author-written manifest is drafted
the way `--refresh` does it, beside the author's file and never over it, and the branch each
case took is reported. `tests/test_bench_dev_manifest_identity.py` asserts the corpus is
byte-identical after a run rather than leaving that to inspection.

**The digest does not embed the commit, and this was measured rather than assumed.** The JSON
report records `/repository/commit`, so a naive re-run of the report check across a commit
boundary reads as every case regressing on an unchanged tree. Nothing in the manifest scaffold
reads git metadata: one case digests identically outside any repository, inside one, and inside
that repository after its HEAD moved. Measuring a copy outside any work tree keeps it that way
by construction.
