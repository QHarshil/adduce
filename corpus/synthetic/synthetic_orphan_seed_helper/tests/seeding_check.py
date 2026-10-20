import numpy as np

from seeding import set_seed


def check_seeding_repeats() -> None:
    set_seed(0)
    first = np.random.rand(3)
    set_seed(0)
    assert (first == np.random.rand(3)).all()
