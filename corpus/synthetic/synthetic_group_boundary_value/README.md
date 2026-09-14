# synthetic_group_boundary_value

A paper that states its batch size once and mentions the number 256 once, in a
formula where 256 is a divisor rather than a batch size.

```latex
We use a global batch size of 4096 and train on a single pod.
... multiply the learning rate by $\frac{\mbox{batch size}}{256}$ ...
```

Reading the number that follows a keyword takes `256` here, because the
characters after `batch size` are `}}{256` and nothing in them looks like a
sentence boundary. But the keyword ends one argument of `\frac` and the number
opens the next, so they are not a statement. They are two siblings of one
command, and the number belongs to the argument the keyword is not in. The
paper says 4096.

This is not a missing number, it is a **wrong** one, and it reaches a verdict:
`configs/train.yaml` holds `batch_size: 4096`, so before the fix R-DRIFT-001
reported the paper and the code as disagreeing about a value they state
identically.

## The guard is adjacency, and the narrowness is the design

`_crosses_group_boundary` refuses only a group that closes and another that
opens with nothing but whitespace between them, because that is what two
arguments of one command always are.

Allowing any text between the braces takes real values with it. A table header
closing and an italic cell opening (`BLEU} & {\it 28.6}`) is not one command's
two arguments at all, and 28.6 is a number the paper reports.

Only the gap between the keyword and the number is examined. Searching the whole
window instead is wrong in a way every test still passes: a brace opening
*after* the number sets a boundary *before* it, so `Batch size}: 16` and
`learning rate:} 0.003` are refused along with the fractions. The parametrized
cases in `tests/test_collectors_new.py` pin both halves, including a paper that
states its batch size in one clause and the scaling fraction in the next.

## Why this case is asserted in `expectations.yaml`

A drift rule reads prose hyperparameters, so a change to how a prose number is
read moves a verdict here. The case pins R-DRIFT-001 at `pass` and forbids
`fail`, and pins R-DRIFT-003 at `pass` and forbids `partial` — the batch size
has a code counterpart, and the fix must not reach that answer by dropping the
statement instead of dropping the divisor.

```console
adduce check corpus/synthetic/synthetic_group_boundary_value
```

to see one batch size read, and both sides agreeing on it.
