# synthetic_console_script_seeding

Control: `src/mypkg/commands.py` seeds python and numpy inside `train`, which
nothing in the repository calls and which has no `__main__` guard. The only
caller is the `mypkg-train` console script that `pyproject.toml` declares.
`commands.py` is not an entrypoint file name.

R-DET-001 must pass: the launcher pip generates imports `mypkg.commands`, so
the console script counts as a reference.
