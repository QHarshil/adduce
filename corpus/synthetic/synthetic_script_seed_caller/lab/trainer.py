import random

import numpy as np


def fit(seed: int = 0) -> None:
    random.seed(seed)
    np.random.seed(seed)
    print(np.random.permutation(10).tolist())
