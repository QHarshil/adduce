"""First-use ordering diagnostic: does seeding happen before the first draw?

Wraps the seed and draw entry points of ``random``, ``numpy.random``, and
``torch`` (when importable) and logs the order of first use. This is a
targeted wrapper, not ``sys.settrace``: tracing only observes Python-level
call events, so it misses RNG draws inside numpy/torch C kernels and inside
``num_workers > 0`` subprocesses, and it is superseded by ``sys.monitoring``
on Python 3.12+ anyway.

Usage, inside the repository's own environment::

    python -m adduce.dynamic.import_hook your_script.py [args...]

Events are printed to stderr as they first occur; exit code 1 signals a draw
observed before any seed call.
"""

from __future__ import annotations

import functools
import sys
import time
from dataclasses import dataclass, field


@dataclass
class OrderLog:
    events: list[tuple[float, str]] = field(default_factory=list)
    seeded: bool = False
    draw_before_seed: bool = False
    _reported: set[str] = field(default_factory=set)

    def record(self, kind: str, name: str) -> None:
        if name in self._reported:
            return
        self._reported.add(name)
        self.events.append((time.monotonic(), f"{kind}: {name}"))
        print(f"[adduce order] {kind}: {name}", file=sys.stderr)
        if kind == "seed":
            self.seeded = True
        elif kind == "draw" and not self.seeded:
            self.draw_before_seed = True
            print(f"[adduce order] WARNING: first draw ({name}) before any seed call", file=sys.stderr)


LOG = OrderLog()


def _wrap(module: object, attribute: str, kind: str, label: str) -> None:
    original = getattr(module, attribute, None)
    if original is None or not callable(original):
        return

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        LOG.record(kind, label)
        return original(*args, **kwargs)

    setattr(module, attribute, wrapper)


def install() -> OrderLog:
    """Install the wrappers on whichever RNG libraries are importable."""
    import random

    _wrap(random, "seed", "seed", "random.seed")
    for draw in ("random", "randint", "randrange", "shuffle", "sample", "choice", "uniform", "gauss"):
        _wrap(random, draw, "draw", f"random.{draw}")

    try:
        import numpy as np

        _wrap(np.random, "seed", "seed", "numpy.random.seed")
        _wrap(np.random, "default_rng", "seed", "numpy.random.default_rng")
        for draw in ("rand", "randn", "randint", "random", "shuffle", "permutation", "choice", "normal", "uniform"):
            _wrap(np.random, draw, "draw", f"numpy.random.{draw}")
    except ImportError:
        pass

    try:
        import torch

        _wrap(torch, "manual_seed", "seed", "torch.manual_seed")
        for draw in ("rand", "randn", "randint", "randperm", "normal", "bernoulli", "multinomial"):
            _wrap(torch, draw, "draw", f"torch.{draw}")
    except ImportError:
        pass

    return LOG


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m adduce.dynamic.import_hook <script.py> [args...]", file=sys.stderr)
        return 2
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    install()
    import runpy

    runpy.run_path(script, run_name="__main__")
    print(
        f"[adduce order] done: {len(LOG.events)} first-use event(s); "
        + ("DRAW BEFORE SEED detected" if LOG.draw_before_seed else "seeding preceded all observed draws"),
        file=sys.stderr,
    )
    return 1 if LOG.draw_before_seed else 0


if __name__ == "__main__":
    raise SystemExit(main())
