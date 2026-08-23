# synthetic_class_file_macro

A paper whose preamble redefines two class-internal commands inside
`\makeatletter`, which is where `@` is a letter and where nearly all class and
style code lives.

A command name was matched as `[a-zA-Z]+`, so
`\def\@fs@pre{\hrule height.8pt depth0pt \kern2pt}` was **not recognised as a
definition at all**. Nothing removed it and nothing expanded it, so the body stood
in the document as ordinary text — and the prose scan, which reads a number
shortly after a keyword, took the `depth0pt` of a rule's depth as a layer count.
Measured, MoCo and SimSiam each yielded a confident `num_layers = 0.0` from that
one line, which is the same trust-destroying class as DETR's four preamble macros
that definition-stripping already removed.

`\@fs@mid` is the control for the removal's own bounds: it is the same shape and
states no number, so it must go without taking anything with it.

The fix is one character in the name class. It is deliberately **not** made at the
use site: `_MACRO_USE_RE` still refuses `@`, because recognising the definition is
what removes the body from the page, while substituting an `@`-named command at
its uses would put class-internal plumbing back into the text. That choice was
measured rather than assumed — admitting `@` at a use site moves **nothing** on
any of the 34 paper trees in `bench/dev/pairs`, so the conservative reading costs
nothing, and the expectation that this fix would change what expansion substitutes
does not hold on real papers.

**This case is asserted in `expectations.yaml`**, because the phantom is a
hyperparameter and hyperparameters reach rules. The case carries
`results/eval.csv` stating the accuracy its prose states, so the reconciliation
rules have something to reconcile and stay `pass` throughout. Against a source
tree whose name class excludes `@`, and identical in every other respect, three of
the 28 rules move:

- **R-DRIFT-003 `partial`** — "Paper states hyperparameter(s) with no detected
  code counterpart: num_layers", about a number in no rendered document.
- **R-DRIFT-001 `unknown`** — "Paper hyperparameters were extracted but none could
  be matched to code-side values", driven entirely by that phantom.
- **R-DRIFT-002 `pass`** — a clean bill of health on a multi-config ambiguity
  question the paper raises nothing for.

All three are `not-applicable` once the definition is recognised, which is the
honest answer: this paper states no hyperparameter.

The whole-corpus effect is exactly two extractions. Over all 34 paper trees the
only differences are MoCo's and SimSiam's `num_layers = 0.0`; no table cell, no
title, no dataset, no prose metric and no other pair moves. Recall is unchanged at
119/296 with no pair falling.

The two byte-identity instruments split on this case, which is worth knowing about
the instruments. The default JSON report carries findings and reports it as moved
(1 of the 4 it reports over 27 cases). `bench/dev/manifest_identity.py` reports it
**identical**: a drafted manifest carries claims, and a phantom hyperparameter is
not a claim, so that harness is structurally blind to this fault — the mirror of
the confidence-only change the JSON report cannot see.

Kept separate from `synthetic_counter_assignment` deliberately. Both faults surface
as a phantom `num_layers`, so one case carrying both could not tell a regression of
one from a regression of the other.

```console
adduce check corpus/synthetic/synthetic_class_file_macro
```

to see the three drift rules report on nothing.
