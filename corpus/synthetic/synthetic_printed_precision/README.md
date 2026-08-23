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
a trailing zero.** `f"{0.30:.10f}".rstrip("0")` is `"0.3"`, so `0.30` was read as
having one decimal, got a tolerance of 0.05, and 0.34 fell inside it. The
clearest form of the same defect is `values_match(28.0, 28.4)`, which was `True`:
`f"{28.0:.10f}".rstrip("0")` is `"28."`, whose fractional part is empty, so a
value printed to a tenth was compared as though printed to a unit.

So the precision is carried from the parse instead. `PaperValue` records how
many digits the source text printed after the point, and the drift rule passes
it. Here that turns R-DRIFT-001 from **pass** — a claim that a repository agrees
with its paper when it does not — into **fail**.

The second hyperparameter is the control. The paper states a weight decay of
`0.05` and the config holds `0.05`; they agree exactly, and they must still
agree once the tolerance tightens, so the case cannot pass merely by making
every comparison stricter.

Measured over the 34 dev pairs, 1,233 hyperparameter statements carry a printed
precision and **13** are compared with a different tolerance than before. Every
one is a paper that printed a trailing zero: CLIP's `temperature & 100.0`,
gpt-neox's and llama's `gradient clipping of 1.0`, latent-diffusion's
`$\eta = 1.0$`, qlora's `dropout 0.0`, stylegan2-ada's `$\eta = 0.0010$`, t5's
`$84.60$`, DeiT's `$5.10$`.

Scientific notation records no precision and falls back to the old inference.
`1e-4` prints no fractional digits at all, yet states a value to a precision its
decimal expansion is what expresses, so counting its printed digits would claim
a tolerance of 0.5 on a number four orders of magnitude smaller.

**Unlike the paper-side cases in this corpus, this one is asserted by
`expectations.yaml` directly**, because a drift rule reads the paper's
hyperparameter statements and the repository's config. A table-cell fix reaches
no rule; a hyperparameter fix reaches one.
