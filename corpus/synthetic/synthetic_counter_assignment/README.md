# synthetic_counter_assignment

A paper that resets its numbering for an appendix and sets two lengths, in the
shapes DETR's supplementary really uses.

A counter is not a measurement. `\setcounter{tocdepth}{2}` says how deep a table
of contents goes and prints nothing at all — but it puts a keyword and a number
two characters apart, `depth}{2`, and that is precisely the shape the
keyword-proximity scan reads as a value. Measured on DETR, a table-of-contents
depth was reported as `num_layers = 2.0` at full confidence.

Definition-stripping cannot reach any of this, and the reason is structural: these
are *uses* of a command, not definitions of one. Nor is expansion any help, since
none of them is a command the paper defined.

The guard removes the call together with the arguments it declares, because the
arguments are the whole problem. Two properties are asserted separately, each
being a place the removal declines to guess:

- **A malformed call is left entirely alone.** `\setcounter` takes two groups; one
  written with one is broken, and removing the name while leaving its arguments
  standing as text is the failure this exists to prevent rather than a lesser
  version of it.
- **A call inside a `verbatim` block stays put**, because there the assignment is
  what the page displays. That is the same reason expansion skips those regions,
  and it is shared with definition-stripping through one span remover so neither
  can lose the guard the other has.

Eight commands are covered, and every one was measured to produce a phantom
without the guard rather than being added on the strength of resembling the
others. The two that assign nothing are the interesting half: it is the *name*
argument that carries the keyword, so `\newlength{\headsep}` ahead of a sentence
mentioning 4 was read as `num_heads = 4.0`, and removing the call is what stops
the scan reaching into the prose that follows. `tests/test_collectors_new.py`
carries one probe per command and asserts the probe set *equals* the covered set,
so a command cannot join the guard without one — and each is proven live by
mutation, dropping any single entry failing exactly its own probe.

This case plants three of them: `\setcounter{tocdepth}{2}` and
`\addtocounter{secnumdepth}{1}` yield `num_layers` 2 and 1, and
`\setlength{\headsep}{4pt}` yields `num_heads = 4`, so `setlength` — by far the
most common member of the family, 169 uses across the dev papers — is load-bearing
here rather than decorative. `\setlength{\tabcolsep}{6pt}` in the preamble is the
control: an assignment naming no hyperparameter, which must keep naming none.

**This case is asserted in `expectations.yaml`**, because the phantoms are
hyperparameters and hyperparameters reach rules. It carries `results/eval.csv`
stating the accuracy its prose states, so the reconciliation rules have something
to reconcile and stay `pass`. Against a source tree without the guard, three of the
28 rules move — R-DRIFT-003 `partial` about a `num_layers` the paper never states,
R-DRIFT-001 `unknown` because that phantom was the only statement to match, and
R-DRIFT-002 `pass` on a question the paper raises nothing for — and all three are
`not-applicable` once the assignments are removed.

The whole-corpus effect is exactly one extraction: over all 34 paper trees the only
difference is DETR's `num_layers = 2.0`. **The other three counter-shaped
extractions in the dev set are not counters at all** and are deliberately left
alone: `bit` and `deit` state `$\frac{\mbox{batch size}}{256}$`, a real formula
whose denominator is read as the batch size, and DETR's `\oldnew{two 3-layers}{a
3-layer}` is the paper's own two-argument macro. Those are a separate defect.
Recall is unchanged at 119/296 with no pair falling.

As with `synthetic_class_file_macro`, the default JSON report sees this case move
and `bench/dev/manifest_identity.py` reports it identical — a drafted manifest
carries claims, and a phantom hyperparameter is not one. The two cases are kept
apart because both faults surface as a phantom `num_layers`, so one case carrying
both could not tell a regression of one from a regression of the other.

```console
adduce check corpus/synthetic/synthetic_counter_assignment
```

to see the three drift rules report on nothing.
