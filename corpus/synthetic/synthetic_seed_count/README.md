# synthetic_seed_count

Red-team-adjacent control: "averaged over 3 seeds" is a seed *count*, not a
seed *value*; the config's seed: 42 and the 42/43/44 sweep must not be
flagged as drift. R-RES-003 must pass; R-DRIFT-001 must not fail.
