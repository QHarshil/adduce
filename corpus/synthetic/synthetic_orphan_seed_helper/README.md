# synthetic_orphan_seed_helper

Planted fault: `seeding.py` defines `set_seed`, which seeds python and numpy,
but no entrypoint calls it and no non-test module imports it. Only
`tests/seeding_check.py` calls it. `train.py` draws from numpy's global RNG.

R-DET-001 must not pass: a helper nothing outside the tests references does
not seed a run. The finding must say that seeding code exists and name the
helper, not report that no seeding was detected.
