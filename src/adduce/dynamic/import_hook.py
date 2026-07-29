"""First-use ordering diagnostic: does seeding happen before the first draw?

Wraps the seed and draw entry points of ``random``, ``numpy.random``, and
``torch`` (when importable) and logs the order of first use. This is a
targeted wrapper, not ``sys.settrace``: tracing only observes Python-level
call events, so it misses RNG draws inside numpy/torch C kernels and inside
``num_workers > 0`` subprocesses, and it is superseded by ``sys.monitoring``
on Python 3.12+ anyway.

Usage, inside the repository's own environment::

    adduce-rng-audit --yes your_script.py [args...]

Events are printed to stderr as supported module-level calls first occur; exit
code 1 signals an observed draw before a deterministic seed for its RNG family
or an entropy-based seed such as ``seed(None)``. Generator-instance methods
and library-internal or native RNG draws are outside this best-effort hook. The
target is executed unsandboxed with the current user's environment and host
access; ``--yes`` is required before RNG libraries or the target are imported.
"""

from __future__ import annotations

import functools
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

_WRAPPER_MARKER = "__adduce_order_wrapper__"
_MISSING = object()
KindResolver = Callable[[tuple[object, ...], dict[str, object]], str]

_PYTHON_RNG = "python"
_NUMPY_GLOBAL_RNG = "numpy-global"
_NUMPY_GENERATOR_RNG = "numpy-generator"
_TORCH_RNG = "torch"
_EXECUTION_WARNING = (
    "WARNING: this diagnostic imports RNG libraries and executes the target script "
    "unsandboxed with the current user's environment, filesystem, process, and "
    "network access. Use a disposable, unprivileged container or virtual machine."
)
_USAGE = "usage: adduce-rng-audit --yes <script.py> [args...]"


@dataclass
class OrderLog:
    events: list[tuple[float, str]] = field(default_factory=list)
    seeded_families: set[str] = field(default_factory=set)
    draw_before_seed_families: set[str] = field(default_factory=set)
    entropy_seed_families: set[str] = field(default_factory=set)
    _reported: set[tuple[str, str, str]] = field(default_factory=set)

    @property
    def seeded(self) -> bool:
        """Whether at least one observed RNG family was seeded."""
        return bool(self.seeded_families)

    @property
    def draw_before_seed(self) -> bool:
        """Whether any RNG family drew before its own seed event."""
        return bool(self.draw_before_seed_families)

    @property
    def uncontrolled(self) -> bool:
        """Whether any observed family used entropy or drew before a seed."""
        return bool(self.draw_before_seed_families or self.entropy_seed_families)

    def reset(self) -> None:
        """Clear observations while preserving references to the shared log."""
        self.events.clear()
        self.seeded_families.clear()
        self.draw_before_seed_families.clear()
        self.entropy_seed_families.clear()
        self._reported.clear()

    def record(self, kind: str, name: str, family: str = "unknown") -> None:
        event = (family, kind, name)
        if event in self._reported:
            return
        self._reported.add(event)
        self.events.append((time.monotonic(), f"{kind}: {name}"))
        print(f"[adduce order] {kind}: {name} [family={family}]", file=sys.stderr)
        if kind == "seed":
            self.seeded_families.add(family)
        elif kind == "entropy":
            self.entropy_seed_families.add(family)
            print(
                f"[adduce order] WARNING: entropy-based seed ({name}) for RNG "
                f"family {family}",
                file=sys.stderr,
            )
        elif kind == "draw" and family not in self.seeded_families:
            self.draw_before_seed_families.add(family)
            print(
                f"[adduce order] WARNING: first draw ({name}) before a seed "
                f"for RNG family {family}",
                file=sys.stderr,
            )


LOG = OrderLog()


def _wrap(
    module: object,
    attribute: str,
    kind: str | KindResolver,
    label: str,
    family: str,
) -> None:
    original = getattr(module, attribute, None)
    if (
        original is None
        or not callable(original)
        or getattr(original, _WRAPPER_MARKER, False)
    ):
        return

    @functools.wraps(original)
    def wrapper(*args, **kwargs):
        event_kind = kind(args, kwargs) if callable(kind) else kind
        result = original(*args, **kwargs)
        LOG.record(event_kind, label, family)
        return result

    setattr(wrapper, _WRAPPER_MARKER, True)
    setattr(module, attribute, wrapper)


def _seed_kind(args: tuple[object, ...], kwargs: dict[str, object]) -> str:
    seed = args[0] if args else kwargs.get("seed", kwargs.get("a", _MISSING))
    return "seed" if seed is not _MISSING and seed is not None else "entropy"


def _successful_system_exit(code: object) -> bool:
    return code is None or (isinstance(code, int) and code == 0)


def install() -> OrderLog:
    """Install the wrappers on whichever RNG libraries are importable."""
    import random

    _wrap(random, "seed", _seed_kind, "random.seed", _PYTHON_RNG)
    for draw in ("random", "randint", "randrange", "shuffle", "sample", "choice", "uniform", "gauss"):
        _wrap(random, draw, "draw", f"random.{draw}", _PYTHON_RNG)

    try:
        import numpy as np

        _wrap(np.random, "seed", _seed_kind, "numpy.random.seed", _NUMPY_GLOBAL_RNG)
        _wrap(
            np.random,
            "default_rng",
            _seed_kind,
            "numpy.random.default_rng",
            _NUMPY_GENERATOR_RNG,
        )
        for draw in ("rand", "randn", "randint", "random", "shuffle", "permutation", "choice", "normal", "uniform"):
            _wrap(np.random, draw, "draw", f"numpy.random.{draw}", _NUMPY_GLOBAL_RNG)
    except ImportError:
        pass

    try:
        import torch

        _wrap(torch, "manual_seed", "seed", "torch.manual_seed", _TORCH_RNG)
        for draw in ("rand", "randn", "randint", "randperm", "normal", "bernoulli", "multinomial"):
            _wrap(torch, draw, "draw", f"torch.{draw}", _TORCH_RNG)
    except ImportError:
        pass

    return LOG


def main() -> int:
    print(f"[adduce order] {_EXECUTION_WARNING}", file=sys.stderr)
    if len(sys.argv) < 2 or sys.argv[1] != "--yes":
        print("[adduce order] refusing execution without explicit --yes", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 2
    if len(sys.argv) < 3:
        print(_USAGE, file=sys.stderr)
        return 2
    import runpy

    script = sys.argv[2]
    target_argv = sys.argv[2:]
    original_argv = sys.argv
    LOG.reset()
    sys.argv = target_argv
    try:
        install()
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exc:
            if not (LOG.uncontrolled and _successful_system_exit(exc.code)):
                raise
    finally:
        sys.argv = original_argv
        print(
            f"[adduce order] done: {len(LOG.events)} first-use event(s); "
            + (
                "UNCONTROLLED RNG USE detected"
                if LOG.uncontrolled
                else (
                    "deterministic seeding preceded all observed draws"
                    if LOG.events
                    else "no RNG calls observed"
                )
            ),
            file=sys.stderr,
        )
    return 1 if LOG.uncontrolled else 0


if __name__ == "__main__":
    raise SystemExit(main())
