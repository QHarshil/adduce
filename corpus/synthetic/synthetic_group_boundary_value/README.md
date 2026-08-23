# synthetic_group_boundary_value

A paper that states its batch size once and mentions the number 256 once, in a
formula where 256 is a divisor rather than a batch size.

```latex
We use a global batch size of 4096 and train on a single pod.
... multiply the learning rate by $\frac{\mbox{batch size}}{256}$ ...
```

Reading the number that follows a keyword takes `256` here, because the sixteen
characters after `batch size` are `}}{256` and nothing in them looks like a
sentence boundary. But the keyword ends one argument of `\frac` and the number
opens the next, so they are not a statement — they are two siblings of one
command. The paper says 4096.

This is not a missing number, it is a **wrong** one, and it reaches a verdict:
`configs/train.yaml` holds `batch_size: 4096`, so before the fix R-DRIFT-001
reads **fail** and accuses a repository that agrees with its paper of drifting
from it. With the fix it reads **pass**.

Both real papers behind this are in the dev set and both are misread the same
way. BiT writes `$\frac{\mbox{batch size}}{256}$` and DeiT writes
`\frac{\mathrm{lr}}{\mathrm{batchsize}}{512}`, and **both state a batch size of
4096**. Measured across all 34 pairs the rule refuses **four** candidates and
every one is wrong: those two, and two from DETR's `\oldnew{old}{new}` revision
macro, where the keyword ends the old text and the number opens the new —
`schedule.}{for 500 epochs`, an epoch count read as a schedule, and `two
3-layers}{a 3-layer`, an FFN's depth read as the model's.

**The braces must be adjacent up to whitespace, and that narrowness is the
design rather than caution.** Two arguments of one command are written against
each other. A brace that closes and one that opens with anything between them is
two separate pieces of markup, and the number after it is routinely real:
fairseq states a BLEU of 28.6 as `BLEU} & {\it 28.6`, a table header closing and
an italic cell opening. Allowing any text between the braces was measured and
rejected — it refuses 13 and takes that value with it.

Only the gap between the keyword and the number is examined, never the whole
window. The window-wide version was written first and **every gate passed while
it destroyed real values**: a brace opening after the number set a boundary
before it, so BERT's `Batch size}: 16`, `epochs}: 2` and `Learning rate (Adam)}:
5e-5` were refused along with the fractions. Nothing caught it, because the
all-pair inventory counts table cells and claims and a hyperparameter is
neither. It was found by listing every candidate the rule would refuse and
reading them, which is the check this class needs.

**Unlike the paper-side cases in this corpus, this one is asserted by
`expectations.yaml` directly.** A drift rule reads the paper's hyperparameter
statements and the repository's config, so a fix to how a prose number is read
does move a verdict here. That is the distinction the entry for
`synthetic_wrapped_table_header` draws: a table-cell fix reaches no rule, a
hyperparameter fix reaches one.
