# synthetic_spaced_config_key

A repository whose config records the paper's ablation under the paper's own name
for it, `dec. depth`, where the space after the abbreviating full stop decided
whether the key named a hyperparameter at all.

A dotted config key resolves on its terminal segment, so `optim.lr` is a learning
rate. The segment was taken unsplit from the separator and unstripped, so
`dec. depth` yielded `" depth"` — and `"depth"` names `num_layers` while
`" depth"` names nothing, on a character that belongs to neither. MAE heads its
own ablation column `dec. depth`, and a config recording that ablation writes the
key the same way.

The cost is not a missing name. It is a false verdict about the paper: the
decoder depth of 8 that the paper states *is* in the repository, in
`configs/ablation.yaml`, and the rule reported it as having no counterpart in
code. `mask_ratio` beside it is the control — an ordinary key, resolved either
way — and `encoder` is the second control, a key naming no hyperparameter, which
must keep naming none.

**This case is asserted in `expectations.yaml`**, because a config key reaches
rules directly. Against a source tree that leaves the terminal segment
unstripped, and identical in every other respect, two of the 28 rules move:

- **R-DRIFT-003 `partial`** — "Paper states hyperparameter(s) with no detected
  code counterpart: num_layers", about a value the repository holds outright.
- **R-DRIFT-001 `unknown`** — "Paper hyperparameters were extracted but none
  could be matched to code-side values", because the one statement the paper
  makes had nothing left to match against.

Both become `pass`, R-DRIFT-001 on the agreement itself: the config says 8 and
the paper says 8, so this case adds no drift finding and none is wanted. A
disagreement would have made the same point with a `fail`, and would have made
the case a drift fixture rather than this one.

**No repository in the dev set writes a key of this shape.** Measured over the
twenty labelled pairs, `canonical_hyperparameter` is asked about 3,839 keys, 1,090
of them distinct, drawn from config files, materialised run configs, CLI
arguments and dataclass fields — and **not one resolves differently**. Recall is
unchanged at 119/296 with no pair falling, and no claim, cell or paper value moves
anywhere. So this case is the only place the path is exercised, which is the
reason it exists.

The two byte-identity instruments disagree about this change, and that is worth
knowing about the instruments. `bench/dev/manifest_identity.py` reports **no case
moved**: a drafted manifest carries claims, not findings, so it is structurally
blind to a rule verdict and cannot see this at all. The default JSON report
carries findings, and reports **this case and no other**, with its claim count
unchanged at 0. Both measured at the 24 cases the corpus held when this was
added. A change that moves only a verdict needs the report, exactly as a
change that moves only a confidence needs the manifest.

```console
adduce check corpus/synthetic/synthetic_spaced_config_key \
  --paper corpus/synthetic/synthetic_spaced_config_key
```

to see the two drift findings.
