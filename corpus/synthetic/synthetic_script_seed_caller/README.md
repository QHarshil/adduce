# synthetic_script_seed_caller

Control: `lab/trainer.py` seeds python and numpy inside `fit`. Its only caller
is `bin/lab-train`, a Python script with a shebang and no `.py` suffix.
`trainer.py` has no `__main__` guard and is not an entrypoint file name.

R-DET-001 must pass: an extensionless file with a Python shebang is project
code, so its import of `lab.trainer` counts.
