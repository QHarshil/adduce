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
`(metric, value)`, the same key staleness uses; a verdict that cannot be joined unambiguously is
counted separately as `unjoined_false_positives` rather than guessed at. **Measured over the four
adjudicated pairs the figure is 109 of 273 false positives, so the criterion is currently met by
no pair.**

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
