# synthetic_power_of_ten

A paper that states its learning rate and weight decay as powers of ten, and a
config that holds the same values in decimal.

```
paper   setting the learning rate to $10^{-4}$ and the weight decay to $10^{-4}$.
        We use a learning rate drop after 400 epochs. For fine-tuning we
        decay the learning rate by a factor of 10 at epoch 30.
        We report results after 300 epochs on 8 GPUs. The head uses a
        learning rate of 1$\times$10$^{-4}$. We linearly warm up the learning
        rate from $10^{-6}$ to $10^{-4}$, then decay the learning rate to
        $10^{-5}$ with a cosine schedule.
config  learning_rate: 0.0001
        weight_decay: 0.0001
        epochs: 300
```

Both sides agree, and adduce reported mismatches.

**`$10^{-4}$` was read as 10.** The number pattern tried a decimal mantissa
before a bare power. Alternation takes the first branch that matches at a
position, and the mantissa branch matches the `10` and stops, because its
exponent is optional. A number followed by `^` is now always read as a base:

- Base 10 with a whole-integer exponent: the power. Math delimiters, braces
  and spacing commands may split it, so `10$^{-4}$`, `1$\times$10$^{-4}$` and
  `$5\,\times\,10^{-4}$` read as powers. They were read as 10, 1 and 5.
- Any other base is not joined across `$`, so `224$^2$` is still 224.
- An exponent with no digit is a marker, so `$76.1^{*}$` and `$10^\dagger$`
  read as their base. On a base other than 10, an uncertainty does too:
  `$76.1^{\pm 0.2}$`, `$28.4^{+0.3}_{-0.2}$`.
- Anything else yields no value, never a fragment: `$10^{-4.5}$`, `$2^{11}$`,
  `$224^2$`.

**Schedule points, deltas and ranges were read as values.** `learning rate
drop after 400 epochs` was a learning rate of 400 and a training length of
400, and `decay the learning rate by a factor of 10` a learning rate of 10.
The word that introduces the number decides:

- `by N`, `after N`, `every N`: a delta, a factor or a schedule point. Refused
  for every keyword, on either side of it, and the keyword then yields
  nothing. `followed by N`, `preceded by N` and `accompanied by N` introduce
  the next stage, so a number before the keyword is read.
- `from X to Y`: for a hyperparameter, nothing, because neither end need be
  the configured value. For a metric, Y, the stated result: `WER is reduced
  from 5.1 to 4.2` states 4.2. A bare `from X` with no `to` after it reads X.
- `to N` with a change verb (`drop`, `decay`, `multiplied`, ...) in the same
  sentence, before the keyword or between it and the number: for a
  hyperparameter a final or peak value, so refused. For a metric the stated
  result, so read. `up to N` is a bound, not `to`.
- Any other number after a change verb is refused.

Nothing after the number is examined: `a learning rate of 0.001, dropped by
10x at epoch 30` still states 0.001.

Refusing every number introduced by `after` costs a true reading. `after 300
epochs on 8 GPUs` states the run length, and was read as 300, agreeing with
`epochs: 300`. It is no longer read, so this case compares no training length.

## Why this case is asserted in `expectations.yaml`

A drift rule reads prose hyperparameters, so each reading moved a verdict. The
case pins R-DRIFT-001 at `pass` and forbids `fail`. The forms covered, and the
controls, are in `tests/test_collectors_new.py`.

```console
adduce check corpus/synthetic/synthetic_power_of_ten
```

to see R-DRIFT-001 report the two rates agreeing.
