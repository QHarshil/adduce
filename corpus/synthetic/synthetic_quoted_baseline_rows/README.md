# synthetic_quoted_baseline_rows

Two results tables in which some rows report numbers the paper credits to prior
work, marked the two ways LaTeX markup can mark them.

This is the shape behind the largest single cost the claim extractor carries.
Adjudicated over four paper/code pairs, 107 of the 109 extractions that were
both confident and wrong are numbers the paper really prints and really
attributes to somebody else. The reading is correct — the header names a metric
and the cell states a value — and what is wrong is offering a competitor's
result as a claim about this artifact.

Two signals are visible to a parser, and both are here.

A **citation in the row label** names the paper a row came from. `BiDAF` and
`Swin-T` carry one, in the two spellings that differ most (`\cite` and
`\citep`), and the cells beside them drop to `lexical_match` at 0.5. The
detection has to happen before the cell cleanup runs, because the cleanup
dissolves the command and leaves the bibliography key against the label, where
nothing can tell it from part of a model's name — which is why `BiDAF` arrives
as `BiDAF~seo2017bidirectional`.

A **full-width section row** partitions a table into senses. The first table
here is BERT's SQuAD shape: `Published` marks the rows beneath it as somebody
else's, and `Ours` marks the end of that stretch. `R.M. Reader` is the row that
matters, because it carries no citation at all: only the section above it says
whose number that is. `Ours` needs no vocabulary of its own — a heading that
names no prior work clears the sense the heading above it set, and that is the
whole of what `Ours` has to do.

The second table is the control, and it is a refusal rather than a recovery. It
is partitioned in exactly the same way, by `ImageNet-22K pre-trained` — which
names a pre-training corpus and says nothing about who produced anything.
ConvNeXt has five such rows, DINO partitions by supervision regime and BLIP by
evaluation setting, so an unrecognised section heading is the common case by a
wide margin. It must clear the sense rather than continue it and rather than
mean "baseline": either would demote a paper's own results across whole tables.
`TinyNet` beneath it therefore keeps `direct_parse` at 1.0, exactly as `TinyNet`
beneath `Ours` does, while `Swin-T` beside it is demoted by its citation alone.

Read correctly, six cells are demoted and four are not. Demotion is the whole of
the effect: every one of the ten cells is still a claim, at the same metric and
the same value and the same location, and only how confidently it was read
moves. That is deliberate. The detector is a heuristic over markup, so a false
positive in it would silently destroy a real own result — the more expensive
mistake — and demotion costs no recall, because matching a claim to a reported
number reads the metric and the value and never the confidence.

**The `--paper` byte-identity comparison cannot see this case, and that is worth
stating rather than discovering.** The default JSON report carries a claim's
metric, value, location and trail and carries neither its confidence nor its
resolution method, so every synthetic case renders identically across this
change — a true negative, not a check. Nor does `expectations.yaml` reach it:
`tests/test_synthetic_corpus.py` calls `run_check` without a paper path, so the
LaTeX collector never runs there, and the entry pins only that an unread paper
yields no verdict. What does carry the demotion is the manifest, which records
both fields:

```console
adduce manifest corpus/synthetic/synthetic_quoted_baseline_rows \
  --paper corpus/synthetic/synthetic_quoted_baseline_rows
```

Ten drafted claims, six at `confidence: 0.5` / `resolution_method:
lexical_match` and four at `1.0` / `direct_parse`.

Measured against that, the case moves under each half of the change separately.
Making the citation pattern match nothing restores `Swin-T` to 1.0 — two cells,
and only two, because `BiDAF` sits under `Published` and stays demoted by the
section alone. Emptying the prior-work section vocabulary restores `R.M. Reader`
— again two, because `BiDAF` keeps its citation. Letting an unrecognised heading
continue the sense above it instead of clearing it demotes `TinyNet` in the
first table, two cells the change must not touch. Removing the demotion
altogether moves all six. Dropping a prior-work cell rather than demoting it
destroys six claims outright, which is the rejected design and the reason the
count of claims is asserted here at all.
