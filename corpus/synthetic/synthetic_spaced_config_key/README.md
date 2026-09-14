# synthetic_spaced_config_key

A repository whose config records a hyperparameter under an abbreviated name,
`optim. lr`, where the space after the abbreviating full stop decided whether
the key named a hyperparameter at all.

```
paper   We use a learning rate of 0.001 and a weight decay of 0.05 ...
config  "optim. lr": 0.001
        weight_decay: 0.05
```

A dotted config key resolves on its terminal segment, so `optim.lr` is a
learning rate. The segment was taken unsplit from the separator and unstripped,
so `optim. lr` yielded `" lr"` — and `"lr"` names `learning_rate` while `" lr"`
names nothing, on a character that belongs to neither.

The cost is not a missing name. It is a false verdict about the paper: the
learning rate of 0.001 that the paper states *is* in the repository, in
`configs/ablation.yaml`, and the rule reported it as having no counterpart in
code.

A separator followed by a space is how a paper abbreviates rather than how a
config nests, and a config recording a paper's own naming writes the key the
same way the paper heads its column.

## The controls

`weight_decay` beside it is the first control: an ordinary key, resolved either
way, so the fix must not be reaching its answer by loosening the lookup for
everything. `encoder` is the second, a key naming no hyperparameter, which must
keep naming none — stripping the segment resolves it, it does not widen the
vocabulary.

## Why this case is asserted in `expectations.yaml`

The config side of a drift comparison is what moves here, so the verdict moves
with it. The case pins R-DRIFT-003 at `pass` and forbids `partial`: both stated
hyperparameters have a code counterpart. R-DRIFT-001 is pinned at `pass`,
because once the key resolves the two values agree.

```console
adduce check corpus/synthetic/synthetic_spaced_config_key
```

to see R-DRIFT-003 name both hyperparameters as having counterparts.
