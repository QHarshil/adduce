# synthetic_counter_assignment

A paper that declares and steps LaTeX counters and lengths whose names carry a
hyperparameter keyword.

A counter is not a measurement. `\newcounter{layers}`, `\stepcounter{layers}`
and `\newlength{\headsep}` print nothing, so they state no number the paper
reports. But each puts a keyword (`layers`, `heads`) in front of whatever number
the prose states next, and the keyword scan reads that number as the
hyperparameter's value. This is a *use* of a command, not a definition of one.

Measured on this line with the guard removed, the paper reads
`num_layers = 4` (from "Table 4") and `num_heads = 2` (from "Appendix 2"), and
three rules move:

- **R-DRIFT-003 `partial`**, about hyperparameters the paper never states.
- **R-DRIFT-002 `pass`**, a pass that is not earned.
- **R-DRIFT-001 `unknown`**.

With the guard, the paper states no hyperparameter and all three drift rules
are not applicable. `\setlength{\tabcolsep}{6pt}` is the control: an assignment
naming no hyperparameter, which must keep naming none.

The two-argument commands (`\setcounter`, `\addtocounter`, `\setlength`,
`\addtolength`) are removed too. On this line `_crosses_group_boundary` also
refuses their value, because it sits in the next brace group, so this case uses
the one-argument commands, where the stripper is the only guard.
`tests/test_collectors_new.py` has a probe for each of the eight commands.
