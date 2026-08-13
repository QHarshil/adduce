# Claim extraction

adduce reads the numbers an artifact reports and records where each one was
stated. That is the whole of this layer. It does not decide whether a number is
correct, whether the repository produced it, or whether it is the paper's own
measurement rather than a baseline quoted from prior work.

Extraction is offline and deterministic: the same bytes yield the same claims,
in the same order, whatever order the collectors ran in.

## What a claim is here

A claim is a number stated somewhere a reader would take as a reported result,
together with what it is called, where it was found, and how confidently it was
read. Reading is not inferring, and the difference is carried in the same
`resolution_method` vocabulary the [evidence graph](aeg-schema.md) uses: a cell
under a column literally headed `Accuracy` is a `direct_parse` and may carry
confidence `1.0`; a number recovered from a sentence by regular expression is a
`lexical_match` at `0.5` and is refused full confidence at construction.

Extraction is exhaustive. Nothing is truncated, because a dropped claim is
indistinguishable from a claim that was never made; the caller decides what to
show.

## Where a number can come from

| source | method | confidence |
| --- | --- | --- |
| `latex_prose` — a metric keyword and a nearby number in a sentence | `lexical_match` | 0.5 |
| `latex_table` — a `tabular` cell under a header that names a known metric | `direct_parse` | 1.0 |
| `latex_table` — a cell whose metric came from the table's caption, or whose header reads like a metric the vocabulary does not know | `lexical_match` | 0.5 |
| `markdown_table` — a GFM cell under a header that names a known metric | `direct_parse` | 1.0 |

That list is closed. Notebook outputs, logged result files, config files and
source code are not claim sources; see [Limits](#limits).

### Paper prose

The LaTeX collector matches a metric vocabulary against the text and takes the
number immediately before the keyword when there is one, otherwise a number
shortly after it (`accuracy of 92.4`). Every match it recovers becomes a
candidate. The matched text is retained as the claim's text, bounded at 120
characters.

Prose extraction cannot tell a reported result from a cited one. "`BERT
achieves 88.5`" and "`we achieve 88.5`" are the same shape to a regular
expression, so both are extracted and both are `lexical_match`.

### `tabular` cells

Tables are the richer source: a results table states the numbers an abstract
only summarises. `tabular`, `tabular*`, `tabularx` and `longtable` are read.
`\multicolumn` and `\rotatebox` are dissolved to the text they wrap, and a
spanning header's name is repeated across the columns it covers so the header
row stays in step with the body rows. The first cell of each row is taken as
the row label; the rest are read as values, and a cell is a value only when
what remains after markup is dissolved is exactly a number.

### Markdown tables

A GFM table is recognised by its delimiter row (`|---|:---:|`), which is what
makes the line above it a header rather than an ordinary line containing pipes.
Cells may carry a percent sign, a spread, thousands separators or exponent
notation. Only documents whose basename is `README.md`, `results.md`,
`benchmarks.md`, `benchmark.md` or `leaderboard.md` are read, matched
case-insensitively at any depth.

That scope is a cost bound and not a precision one, and the measurement says
so: reading all 1,403 markdown files of `transformers` instead of its one
README yields the same zero candidates, because the header requirement below
already rejects all 2,351 numeric cells in that tree.

## Only the files the paper compiles

The include graph is resolved from the `\documentclass` roots, following
`\input` and `\include` the way LaTeX resolves them — implied `.tex`
extension, the including file's directory before the tree root. A superseded
draft left in a source tarball reaches no rendered page, so its numbers state
no claim and are not read.

Where the graph explains nothing — no root, or a root that reaches no other
file — every `.tex` file is read instead, because no evidence at all is the
worse failure. Comments are stripped first, so a commented-out table states
nothing.

## A column header must name a metric

This is the single requirement that separates a claim extractor from a number
scraper, and it is also the main reason recall is not higher.

A header is canonicalised by literal lookup against the shared metric
vocabulary in `adduce.naming`, which resolves `Top-1`, `Acc.` and `accuracy`
onto one name, strips a trailing unit or arrow (`Accuracy (%)`, `F1 ↑`), and
falls back to the trailing words of a qualified header (`SQuAD1.1 EM`) while
matching the whole name first so that `word error rate` is not flattened onto
`rate`.

A header that canonicalises makes the cell a `direct_parse`. A header that
fails to canonicalise but still reads like the name of a metric — `Throughput`
is real and merely absent from the vocabulary — is kept as a `lexical_match`,
because dropping it would lose a reported number instead of abstaining on it.
A header that is not a metric name under any vocabulary is skipped outright:

- a positional placeholder the table parser filled in (`col4`)
- undissolved LaTeX residue — a brace, a backslash, a `key=value` directive, a
  leading span-and-alignment, which arrive concatenated with the visible text
  as headers like `1c[origin=rc]270coraal`
- a split word — `test`, `dev`, `val` — which names what the number was
  measured on, not what was measured
- a header longer than 40 characters, which is a caption the parser mis-split
- a header carrying no letter at all

Emitting every header verbatim was measured over ten real papers at 4,383
candidates of which 4.6% named a known metric; the rest were placeholders,
empty strings and markup. None of those is a claim to abstain on. In markdown
the same rule holds twice over: a table none of whose headers names a metric is
skipped entirely, which is why `transformers`' argument tables, shape tables and
version matrices yield nothing while `nanogpt`'s eight train and validation
losses are kept.

## When the caption names the metric

A results column often heads a *dataset* — `CORAAL`, `LAMBADA`, `SQuAD 1.1
dev` — while the metric is stated once, in the caption. On `whisper`, every one
of its 121 distinct extracted metric names was a dataset name while the caption
read "word error rate (WER)".

So where the header names no metric and the enclosing float's caption names
exactly one, the cell takes the caption's metric and keeps the header as its
column label. Three constraints hold:

- **Exactly one.** A caption naming two metrics does not say which column
  reports which, and guessing states a confident wrong name where abstaining
  states none.
- **A header that canonicalises is never overridden.** The column is the more
  specific statement.
- **The caption never revives a cell the header filter dropped.** It renames a
  candidate that would have been kept anyway.

**A caption-derived metric is never certain.** Measured over the development
set, the caption rule renames about 2,259 cells correctly and about 194 wrongly
— roughly 8%. The wrong ones are cost and size columns rather than datasets: a
`hours` or `speedup` column under a caption describing the authors' training, a
`throughput (image/s)` column under a caption naming both throughput and
accuracy. Nothing in a header alone separates a dataset column from a cost
column, so the rule cannot be made exact; what it must not do is assert those
194 at confidence `1.0`. It is emitted as `lexical_match` at `0.5`.

## When the metric is one row lower

A table whose columns are headed by datasets often names the metric in a second
header row. That row is composed onto the first — `LAMBADA ppl` — and both
parts are kept, because the composed header canonicalises on its trailing
metric word where the dataset alone canonicalises to nothing.

Three conditions are all required before a second row is read as a header: it
states no number in any column, no cell of the first row names a metric, and
some cell of the second row does. A body row of a results table fails the
first, a table whose columns are already metrics fails the second, and a units
or group-label row fails the third. The first condition also bounds the cost of
a wrong answer: a row stating no number yields no cell either way, so at worst
this renames the columns beneath it.

## The same claim stated twice

A paper states its headline number in the abstract, again in a results table,
and often a third time in the repository README. Those are three statements of
one claim, and counting them as three inflates everything downstream.

Candidates are grouped by metric and by value, where two values agree if they
are equal at the precision of the *less* precise one — a paper writing `92.4`
and a log writing `92.41` are agreeing. The more precise statement becomes the
cluster's value; every member keeps its own location, so an author can be shown
all three places a number appears. A cluster reports the best method any member
carries, because one direct parse is enough to know the number was really
stated.

Two rules bound the merging:

- **Two different numbers stated at one location are never one claim.** Two
  cells of a single table are two measurements reported side by side, however
  closely they round together. Identical values at one location still merge: a
  table repeating a number across two columns states it twice.
- **Cross-unit reconciliation is not done here.** `0.924` and `92.4` are left
  as two claims. Reconciling them is a later stage with its own resolution
  method, and doing it silently at clustering time would let an inference
  masquerade as a parse.

## What extraction produces

Extracted claims are drafted into `.adduce/manifest.yaml` by `adduce manifest`,
numbered by the earliest location that states each one. `adduce check` renders
the same drafts as claim trails marked `[inferred draft]` when no manifest
exists yet, and `adduce package` carries a scaffolded manifest into the
reviewer packet when the repository has none.

```yaml
claims:
  - id: C3
    text: "Swin-B: top-1 acc = 84.5"
    kind: metric
    where: "paper/main.tex:214"
    metric: accuracy
    value: 84.5
    produced_by:
      command: "python train.py"
      config: configs/base.yaml
      log: results/eval.csv
    status: draft
    confidence: 1.0
    resolution_method: direct_parse
```

`confidence` and `resolution_method` record how the number was read, in the
same vocabulary as the table above, so a reader downstream can tell a parsed
cell from a number recovered by regular expression. Both are optional: a
manifest that states neither is valid and loads unchanged, and the placeholder
claim below asserts no number, so it carries neither. A `confidence` outside
`[0, 1]` or a `resolution_method` outside the vocabulary is refused with the
rest of the manifest's validation.

This is the shape of a drafted claim, not captured output. Read it with the
[manifest section of Concepts](concepts.md#the-reproducibility-manifest): every
drafted claim is `status: draft`, which is a placeholder for author
confirmation and not author-confirmed evidence.

`log` is resolved per claim: it names a result file that actually states this
claim's metric at this claim's value, compared with the same rounding
awareness, or nothing at all. `command` and `config` are repository-level
scaffold defaults and remain guesses — resolving those needs the producer
graph, not a numeric comparison.

Where a results table was detected and no claim could be read out of it, one
placeholder claim is drafted saying so. That is a different state from "no
results are reported": the author can supply the metric name, and a missing
alias is a recall bug in the vocabulary rather than a property of the
repository.

Nothing here writes a finding or moves a score. Rules read the manifest the
author confirmed, so extraction improving does not change a repository's
results; author claims are never overwritten, and `adduce manifest --refresh`
writes a separate proposal file that appends only genuinely new drafts.

## How it is measured

Claim extraction is measured on `bench/dev`, a development set of paper and
repository pairs kept deliberately separate from the [validation
corpus](../corpus/README.md), whose claim-carrying repositories are the locked
evaluation set. The method, the label schema and the sampling discipline are
documented in [bench/dev/README.md](../bench/dev/README.md); the two rules that
matter most are that ground truth is transcribed from the *rendered PDF* rather
than from the LaTeX source, and that an extraction matches a label only when
both the value and the canonicalised metric agree, one-to-one.

Measured over the 20 of 34 pairs labelled so far:

| measurement | value |
| --- | --- |
| pooled recall | 97 / 296 = 32.8% |
| recall against labels whose metric the vocabulary can name | 97 / 184 = 52.7% |
| pooled precision, over the 4 pairs adjudicated | 395 / 668 = 59.1% |
| metric names the vocabulary canonicalises | 165 of 296 eligible labels, over 16 names |

Precision per pair, each against an adjudication that corresponds one-to-one with
what the extractor produces today:

| pair | precision | baseline | hyperparameter | not in paper | in repo, not paper |
| --- | --- | --- | --- | --- | --- |
| `detr` | 95 / 130 = 73.1% | 29 | 1 | 5 | 0 |
| `convnext` | 166 / 263 = 63.1% | 70 | 26 | 1 | 0 |
| `bert` | 94 / 154 = 61.0% | 49 | 10 | 1 | 6 |
| `barlowtwins` | 40 / 121 = 33.1% | 78 | 1 | 2 | 0 |

**Quoted baselines are the dominant cost, not fabrication.** 226 of the 668
adjudicated extractions are numbers the paper really reports but attributes to
prior work; only 9 are values the paper does not state at all.

Because a claim now records the confidence it was read at, a stricter question
can be asked: how many of the extractions that are *not* the paper's own result
were nonetheless asserted at confidence `1.0`? Measured over the same four
pairs, **109 of 273**, distributed very unevenly — 3 on `detr`, 25 on `bert`,
38 on `barlowtwins`, 43 on `convnext`. That figure is reported rather than
smoothed: it is the number a reader should weigh before treating a
full-confidence claim as settled, and reducing it is what the caption rule's
demotion to `lexical_match` and the metric-header requirement are both for.

The vocabulary's coverage is concentrated: `accuracy` accounts for 98 of those
165 labels, `map` for 12 and `wer` for 11.

Recall and precision run in opposite directions and need separate evidence.
Recall runs from the paper to the extraction and is answered by the sampled
labels. Precision runs from the extraction to the paper — of the claims the
system produced, how many are real reported own results? — and is answered only
by adjudicating each extraction. **Precision is reported for 4 of the 34 pairs.**
The other 30 report `unavailable` with a reason rather than a fabricated zero —
most because no adjudication exists, and any whose adjudication stopped
corresponding to what the extractor produces likewise reports `unavailable`
rather than a stale figure. An `unavailable` never contributes a zero to an
average.

A precision figure here counts an extraction as correct only when it is the
paper's own reported result. It is therefore a stricter measure than "is this
number in the paper": a quoted baseline is a real printed number, and it still
counts against precision, because surfacing a competitor's figure as a claim
about this artifact would misattribute it.

For a before-and-after: on `barlowtwins`, the LaTeX-prose-only extractor scored
0 of 15 labels. Today it scores 9 of 15.

## Claim text is untrusted input

A claim's text, metric name, row label and column label are repository and
paper content carried verbatim. adduce treats them as untrusted:

- Prose text is bounded at 120 characters where it is read, a caption at 300,
  and a rendered claim trail prints at most 90.
- A claim trail is assembled as rich text objects rather than as markup, so
  console markup in a table cell cannot style or forge terminal output.
- Claim text is never executed and never selects a command. The opt-in
  `reproduce` layer runs the manifest's `smoke.command` or a command you pass
  on the command line, and nothing else.

Bounding is not uniform: a `tabular` row label is carried at whatever length
the table states it.

## Limits

- **The header requirement costs recall, deliberately.** A column that names no
  known metric is skipped, and that is the main reason recall is not higher. A
  missing alias is a recall bug and belongs in the metric vocabulary.
- **112 of 296 eligible labels cannot be canonicalised at all**, which leaves
  the 184 the vocabulary can name as the denominator of the second row above.
  Roughly a quarter of the 112 are dataset names — `CoLA`, `MNLI` — which must
  **not** be added to a metric vocabulary, because a dataset is not a metric: a
  claim named after the data it was measured on does not say what was measured.
- **Notebooks and result files are not claim sources.** A number printed in a
  notebook output cell or written into `results.csv` is never extracted as a
  claim. A result file is consulted only in the other direction, to confirm
  that a claim already extracted from the paper or the README also appears in a
  log.
- **Claim-to-evidence resolution is not implemented.** This layer is extraction
  only. Which artifact produced a number, and whether the artifact still
  produces it, are separate stages that do not exist yet; `log` resolution is
  the one direction settled here and it is a numeric comparison, not a
  traversal.
- **A `tabular` cell's location is the line the table opens on**, not the
  cell's own line, so a drafted claim's `where` points at the table rather than
  at the number.
- **The first column of a `tabular` row is a label, never a value.** A table
  that states a number in its first column loses it.
- **A LaTeX cell that carries a spread is dropped.** `5.6$\pm$0.2` is not
  exactly a number once markup is dissolved. A markdown cell writing the same
  thing is kept, at the point value.
- **Units are recorded for a markdown cell's percent sign only.** A LaTeX
  cell's `\%` is stripped, and nothing then records that the column was a
  percentage.
- **The development set is one labeller's single pass.** No inter-rater
  agreement estimate exists for it and none is reported. That is acceptable for
  a development set and would not be for the evaluation set.
- **These figures are extraction quality, not artifact quality.** They say how
  much of a paper adduce can read. They say nothing about whether any claim is
  reproducible; see [Honest limits](honest-limits.md).
