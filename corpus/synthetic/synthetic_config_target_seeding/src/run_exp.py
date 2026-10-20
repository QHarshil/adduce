import hydra
import numpy as np
from hydra.utils import call


@hydra.main(version_base="1.3", config_path="../configs", config_name="config")
def go(cfg) -> None:
    call(cfg.seeder)
    print(np.random.permutation(10).tolist())


if __name__ == "__main__":
    go()
