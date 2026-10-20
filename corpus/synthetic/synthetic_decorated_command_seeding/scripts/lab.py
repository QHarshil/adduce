import random

import click
import numpy as np


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.option("--seed", default=0)
def fit(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    print(np.random.permutation(10).tolist())


cli()
