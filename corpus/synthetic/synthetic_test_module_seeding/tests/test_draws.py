import random

import numpy as np

random.seed(0)
np.random.seed(0)


def test_draw_is_in_range() -> None:
    assert 0 <= np.random.rand() < 1
