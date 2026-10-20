# synthetic_decorated_command_seeding

Control: `scripts/lab.py` seeds python and numpy inside `fit`, a click command.
Nothing calls or names `fit`, the module calls `cli()` at module scope with no
`__main__` guard, and `lab.py` is not an entrypoint file name.

R-DET-001 must pass: the decorator receives `fit` and registers it, so click
calls it by name from the command line.
