import random

import numpy as np


class Experiment:
    def __init__(self, seed: int) -> None:
        random.seed(seed)
        np.random.seed(seed)

    def go(self) -> list[int]:
        return np.random.permutation(10).tolist()


print(Experiment(0).go())
