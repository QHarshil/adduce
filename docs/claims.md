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
| `latex_table` — a cell whose metric came from the table's caption, whose header reads like a metric the vocabulary does not know, or whose row the paper credits to prior work | `lexical_match` | 0.5 |
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
only summarises. Every `\begin{tabular}` … `\end{tabular}` block is read. Rows
are separated by `\\`, rule commands are removed, and the remaining markup is
dissolved to the text it wraps. The first cell of each row is taken as the row
label; the rest are read as values, and a cell is a value only when what
remains after markup is dissolved is exactly a number.

Every `.tex` file in the repository is read, with comments stripped first, so a
commented-out table states nothing.

### Markdown tables

A GFM table is recognised by its delimiter row (`|---|:---:|`), which is what
makes the line above it a header rather than an ordinary line containing pipes.
Cells may carry a percent sign, a spread, thousands separators or exponent
notation. Only documents whose basename is `README.md`, `results.md`,
`benchmarks.md`, `benchmark.md` or `leaderboard.md` are read, matched
case-insensitively at any depth.

That scope is a cost bound and not a precision one. The header requirement
below runs per table and knows nothing about which file the table came from, so
widening the scope changes how many files are opened, not which tables are
read. What keeps an argument table or a version matrix out of the claim set is
the header requirement, at any scope.

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
fails to canonicalise but still reads like the name of a metric — a `mask
quality rating` is real and merely absent from the vocabulary — is kept as a
`lexical_match`, because dropping it would lose a reported number instead of
abstaining on it.
A header that is not a metric name under any vocabulary is skipped outright:

- a positional placeholder the table parser filled in (`col4`)
- undissolved LaTeX residue — a brace, a backslash, a `key=value` directive, a
  leading span-and-alignment, which arrive concatenated with the visible text
  as headers like `1c[origin=rc]270coraal`
- a split word — `test`, `dev`, `val` — which names what the number was
  measured on, not what was measured
- a header longer than 40 characters, which is a caption the parser mis-split
- a header carrying no letter at all

None of those is a claim to abstain on, because none of them names a metric
under any vocabulary. In markdown the same rule holds twice over: a table none
of whose headers names a metric is skipped entirely, which is why a library's
argument tables, shape tables and version matrices yield nothing while a
README's `train loss` and `val loss` columns are kept.

## When the caption names the metric

A results column often heads a *dataset* — `CORAAL`, `LAMBADA`, `SQuAD 1.1
dev` — while the metric is stated once, in the caption. A speech paper whose
columns are all corpora and whose caption reads "word error rate (WER)" is the
shape this exists for: the metric is nowhere in the header row at all.

So where the header names no metric and the enclosing float's caption names
exactly one, the cell takes the caption's metric and keeps the header as its
column label. A caption belongs to the float it is written in, never to the
nearest table, and the caption a float carries first is its own.

Three constraints hold:

- **Exactly one.** A caption naming two metrics does not say which column
  reports which, and guessing states a confident wrong name where abstaining
  states none.
- **A header that canonicalises is never overridden.** The column is the more
  specific statement, and a caption naming one metric over a table reporting
  several would otherwise rename the ones it does not mean.
- **The caption never revives a cell the header filter dropped.** It renames a
  candidate that would have been kept anyway.

**A caption-derived metric is never certain.** The column did not state the
metric; another part of the document did. A header naming no known metric may
be a cost column — `hours`, `speedup`, `p-value` — rather than a dataset, and
nothing in a header alone separates the two, so this class cannot be eliminated
here. What it must not be is confident, so a caption-derived metric is emitted
as `lexical_match` at `0.5`. Reporting a wrong metric confidently is the
failure mode that costs the most trust.

## When the paper credits the number to somebody else

A comparison table prints a competitor's result under a header that names a
metric, in a cell that states a value. The *reading* is right; what is wrong is
offering it as a claim about this artifact. Where the collector sees the
attribution in the markup, the cell stays a claim at the same metric, value and
location, and only its method and confidence move, to `lexical_match` at `0.5`.

Demoted rather than dropped, deliberately. The detector is a heuristic over
markup, so a false positive in it would silently destroy a real own result,
which is the more expensive mistake. A demotion costs no recall either, because
matching a claim to a reported number reads the metric and the value and never
the confidence. The demotion moves the method and the confidence and nothing
else: it does not choose a different metric, does not drop the cell, and cannot
revive one the header filter refused.

Only one attribution signal is detected: a citation command in the row's
leading cell, which names the paper the whole row came from. A citation beside
a number is a note on that number and is not read as an attribution of the row.

Everything else is **not** detected, and a cell it would have covered reads as
unattributed — the conservative answer, because the consumer demotes on a
positive and never promotes on a negative, so a missed attribution costs
confidence that was not earned. Two shapes are known to be missed. A table
partitioned into published and own results by a full-width section header
needs a table parser that tracks column spans, which this one does not have. A
row label that credits another paper in plain text rather than through a
citation command — `ResNet-50 (He et al.)` — reads as an ordinary row name, so
a related-work table of competitors' numbers under a header naming a metric is
drafted at full confidence. Both are why the
zero-high-confidence-false-positive acceptance criterion is not met, and a
drafted claim is `status: draft` for exactly this reason.

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

Three rules bound the merging:

- **Two cells naming different measurements are never one claim.** A row and a
  column together name what was measured, so two cells naming different ones
  stay two claims however closely their values round. Two cells naming the same
  measurement are one number stated twice — a baseline row repeated in every
  ablation table is one claim.
- **Two different numbers stated at one location are never one claim.** Every
  cell of one `tabular` records the line the environment opens on, so the
  locator cannot separate them; this is what remains for a number the parse
  could not label. Identical values at one location still merge.
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
rest of the manifest's validation, and so is a `confidence` of `1.0` under a
method that infers rather than reads — the same coupling the extractor is held
to at construction, so a hand-written manifest is not a way around it.

`row_label` and `column_label` name the cell a claim was read from. They come
from the first member of the cluster that names one, which need not be the
member `where` points at: `where` is the earliest location any member carries,
so a number stated both in a sentence and in a table takes one field from each.
Read them as two independent facts about the claim.

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

## How well it works is not stated here

Claim extraction is developmental. Its recall, precision and false-positive
counts are not stated in this document or anywhere else in the tree: protocol
amendment 8 (`corpus/PILOT_PROTOCOL.md`) admits no effectiveness, calibration
or false-positive figure while the unlocked development interval is open, and
the pairs earlier figures were drawn from are not reproducible from this tree,
because the analyzer they were measured against is being rebuilt on purpose.

Two records govern what may be claimed and when.
[ADR 0009](adr/0009-developmental-measurements-while-the-corpus-is-unlocked.md)
holds the restriction for the duration of the interval.
[ADR 0010](adr/0010-the-first-study-measures-a-development-set.md) records that
the first human study is a development-set study — findings from the pilot have
already informed detector changes, so no generalized accuracy claim can come
from it, and a confirmatory result needs a separate holdout frozen before its
results are inspected.

What is settled without a figure is the verdict. The
zero-high-confidence-false-positive acceptance criterion is **not met**, and no
project document, release note or README line should describe it as met. The
properties this layer holds by construction — exhaustiveness, determinism, the
refusal of full confidence for an inferred method, and the abstentions listed
under [Limits](#limits) — are the ones stated on this page.

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
- **A dataset name is not a metric and must not be added as one.** `CoLA` and
  `MNLI` name what a number was measured on, not what was measured, so a claim
  named after them says nothing that could later be resolved. They stay
  unreadable on purpose, and the caption rule is what recovers the metric for
  the columns they head.
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
- **A spanning header is not expanded.** A `\multicolumn` header collapses to
  one column here rather than repeating across the columns it covers, so the
  body rows beneath it are wider than the header and every positional lookup
  would slide by one. Where a body row's width does not match the header's, its
  columns are labelled positionally instead, and the header requirement then
  refuses them — a whole table of numbers lost, deliberately, rather than a
  table of confident wrong metric names.
- **A second header row is not read.** A table that heads its columns with
  datasets and names the metric one row lower is read from the first row alone,
  so its columns canonicalise to nothing unless the caption names the metric.
- **Only `tabular` is read.** `tabularx`, `longtable` and `tabular*` state no
  claims, and neither do the rows of a paper that ends them with a macro it
  defined itself, because macros are not expanded.
- **Units are recorded for a markdown cell's percent sign only.** A LaTeX
  cell's `\%` is stripped, and nothing then records that the column was a
  percentage.
- **These are properties of extraction, not of the artifact.** They say how
  much of a paper adduce can read. They say nothing about whether any claim is
  reproducible; see [Honest limits](honest-limits.md).
