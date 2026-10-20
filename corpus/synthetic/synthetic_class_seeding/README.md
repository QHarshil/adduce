# synthetic_class_seeding

Control: `sweep.py` seeds python and numpy in `Experiment.__init__` and
constructs `Experiment` at module scope. Nothing names `__init__`, there is no
`__main__` guard, and `sweep.py` is not an entrypoint file name.

R-DET-001 must pass: a class named outside its own body counts for every
method in it, including a use at module scope in the file that defines it.
