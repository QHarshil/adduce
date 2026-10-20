# synthetic_config_target_seeding

Control: `src/seeding.py` seeds python and numpy in `seed_all`, which no Python
file calls, imports or names. `configs/seeder/default.yaml` names it as a Hydra
`_target_`, and `src/run_exp.py` calls the configured object.

R-DET-001 must pass: a dotted path in a config file names its last segment.
