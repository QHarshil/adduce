# synthetic_printed_precision

A paper that states a learning rate of `0.30` and code that uses `0.34`. Those
disagree, and adduce reported that they agreed.

```
paper   We train every model with a learning rate of 0.30 ...
config  learning_rate: 0.34
```

`values_match` allows the paper's value to be a rounded form of the code's, so
the tolerance is half of the last place the paper printed. A paper printing
`0.30` has said the rate is 0.30 to a hundredth, which 0.34 is not. A paper
printing `0.3` would have said only that it is 0.3 to a tenth, which 0.34 is.
The two statements are different and the paper made the first one.

**The tolerance was inferred from the parsed float, and a float cannot remember
a trailing zero.** `f"{0.30:.10f}".rstrip("0")` is `"0.3"`, so `0.30` was read
as one decimal and given ten times the tolerance it stated. No amount of
formatting recovers the digit. It has to come from the source text, which is
what `PaperValue.decimals` now carries and `_printed_decimals` reads.

`weight_decay` is the control. It agrees exactly at `0.05` on both sides, so it
must keep agreeing once the tolerance tightens: this fix must not turn an
agreement into a drift.

## Why this case is asserted in `expectations.yaml`

Unlike the paper-side fixtures, this one pins a verdict directly. A drift rule
reads the paper's prose hyperparameters, so a change to how precisely one is
read moves R-DRIFT-001 from `pass` to `fail`, and the `fail` is the correct
answer. R-DRIFT-003 is pinned alongside it at `pass`: both stated
hyperparameters do have a code counterpart, and finding a disagreement between
them is a different question from finding one of them missing.

Scientific notation is deliberately outside this: `1e-4` prints no fractional
digits at all while stating a value to a precision its decimal expansion
expresses, so `_printed_decimals` returns `None` there and the caller infers
what it always did.

```console
adduce check corpus/synthetic/synthetic_printed_precision
```

to see R-DRIFT-001 name the learning rate and not the weight decay.
