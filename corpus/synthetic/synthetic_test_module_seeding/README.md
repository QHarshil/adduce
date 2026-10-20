# synthetic_test_module_seeding

Planted fault: the only seeding sits at module scope in `tests/test_draws.py`.
`train.py` draws from numpy's global RNG without seeding it.

R-DET-001 must not pass: seeding in a test does not seed a run, even at module
scope. The finding names the test file and does not recommend calling it.
