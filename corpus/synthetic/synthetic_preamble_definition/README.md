# synthetic_preamble_definition

A paper whose preamble defines commands of its own, in the four shapes that were
read as results, plus the one number it really does print through a command.

A definition states what a command means. The page never shows it, so a number
written inside one is not a number the paper reports — and expansion cannot
reach it in either direction, because substituting a command at its uses leaves
its definition exactly where it was.

`\iouloss` and `\diceloss` are DETR's shape, and they are why the value is not
even a number: the body states a metric name against the parameter placeholder
the command will be given, so `{\cal L}_{\rm iou}(#1)` is read as an IoU of 1
and `{\cal L}_{\rm DICE}(#1)` as a Dice of 1. DETR carried four such claims, at
confidence 0.5, about numbers in no rendered document.

`\tableofcontents` is the llncs class file, which DETR also carries, and it is
the nesting case: `\authcount` and `\lastand` are defined *inside* its body, so
the spans a scan finds overlap and a caller removing them cannot remove the same
characters twice. The values read are the parameter text `##1` of a counter
assignment and the `2` of the comparison beside it, as an AUC of 1 and an AUC
of 2.

`\aclpaperid` is BERT's shape, and there the artifact is manufactured rather
than merely left in place. Expansion substitutes a command at every use, and one
of those uses is the definition's own name, so `\def\aclpaperid{1584}` becomes
`\def1584{1584}` and its `f1584` is read as an F1 of 584. A submission number is
not a measurement in any reading.

`\parheadsc` is LoRA's shape and the hyperparameter case: the `heads` alias
matches inside the command's own name and the value read is the `[1]` stating
how many arguments it takes, as a head count of 1. Metrics and hyperparameters
are read by one pass, so both are answered by removing the definition.

`\ourbleu` is the control, and it is the constraint the removal has to respect.
The paper prints 70.4 through it, and what a body contributes to the page is
contributed where the command is used — so the number is still read there, at
the line a reader would open to check it, and not at the definition.

Read correctly, the paper states `bleu = 70.4` from the sentence that uses the
command, and `IoU = 82.9` and `Dice = 91.3` for `Ours` from the table. `iou` is
therefore in the case twice: once as an artifact valued 1, once as the number
the paper reports.

**This case is asserted in `expectations.yaml`, and that is unusual for a
paper-side case.** It carries `results/eval.csv`, stating the IoU, Dice and BLEU
the paper reports, so the two reconciliation rules that read the paper's own
prose numbers have something to reconcile against. A number read out of a
definition is then not merely a spurious claim: it is a false accusation. Against
a source tree that reads definitions, and identical in every other respect, five
of the 28 rules move on this case:

- **R-RES-002 `partial`** — "Reported metric(s) materially differ from the logged
  values: iou: paper 1 vs closest logged 82.9; dice: paper 1 vs closest logged
  91.3". The paper is reported as contradicting its own results, on numbers it
  never states.
- **R-RES-004 `partial`** — `auc` and `f1` reported as extracted metrics with no
  logged column, both of them read out of the preamble.
- **R-DRIFT-003 `partial`** — a hyperparameter `num_heads` with no code
  counterpart, which is the `[1]` arity of `\parheadsc`.
- **R-DRIFT-001 `unknown`** — "Paper hyperparameters were extracted but none
  could be matched to code-side values", and **R-DRIFT-002 `pass`** where the
  paper in fact states nothing for either to be about: both driven by that same
  phantom hyperparameter, and both `not-applicable` once it is gone.

The corpus byte-identity comparisons see the claim set behind those verdicts, and
they were **vacuous** for this class before this case: leaving definitions in the
document moves this case and no other, on `bench/dev/manifest_identity.py` and on
the default JSON report alike, with every other case byte-identical either way,
because no other synthetic paper states a number inside a definition. Measured at
the 24 cases the corpus held when this was added. Here it drafts **8 claims
instead of 3** — `iou = 1`, `dice = 1`, `f1 = 584`, `auc = 1` and `auc = 2`,
every one at confidence 0.5, against the three the paper states.

Both instruments are live independently: disabling canonicalisation outright
moves 11 cases on the manifest harness and 9 on the JSON report, at that same 24.
And the JSON report is measured against itself first, since `/repository/root` is
the scratch copy's own path and reads as movement on every case if it is left
in.

That the definitions are what moves it, rather than something else the tree
changed, is asserted directly in `tests/test_collectors_new.py`: each shape
above, the nesting, the line numbers underneath a removed body, and
`\ourbleu`'s number surviving at its use.

Two refusals are deliberate and are asserted separately, because each is a place
the removal declines to guess. A definition whose body cannot be brace-matched
is not removed at all — the document is malformed there and guessing where the
definition ends would delete text the paper prints. And a definition inside a
`verbatim` block is left where it is, because there the definition is what the
page displays; that is the same reason expansion skips those regions, and it
means a paper *showing* a definition can still be read as stating its number.

```console
adduce check corpus/synthetic/synthetic_preamble_definition \
  --paper corpus/synthetic/synthetic_preamble_definition -f json
```

to see the drafted claims, and

```console
adduce manifest corpus/synthetic/synthetic_preamble_definition \
  --paper corpus/synthetic/synthetic_preamble_definition
```

for the confidence and resolution method the JSON report drops.
