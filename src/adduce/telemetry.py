"""In-process stage timing and counters for the check pipeline.

Records where a run spends its time and how much repeated work it does, so a
performance regression is measurable rather than inferred. Nothing here leaves
the process: no network, no writes, no environment inspection.

Collection is always on, because it costs a few dozen clock reads per run.
Reporting is opt-in: a duration is not a reproducible value, and the default
JSON report is compared byte for byte by the validation harness.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Telemetry:
    """Stage durations and counters for one pipeline run.

    Durations accumulate per name, so a stage entered more than once reports
    its total rather than only its last visit.
    """

    #: Whether a reporter may include this telemetry in its output. Off by
    #: default: timings differ between identical runs, and the default report
    #: must stay byte-stable.
    report: bool = False
    _stages: dict[str, float] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time a named stage.

        The duration is recorded even when the body raises, so a failed run
        still shows how far it got.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self._stages[name] = self._stages.get(name, 0.0) + elapsed_ms

    def count(self, name: str, amount: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + amount

    def counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def milliseconds(self, name: str) -> float | None:
        return self._stages.get(name)

    def snapshot(self) -> dict[str, Any]:
        """A deterministically ordered view. Durations are in milliseconds."""
        return {
            "stage_milliseconds": {
                name: round(self._stages[name], 3) for name in sorted(self._stages)
            },
            "counters": {name: self._counters[name] for name in sorted(self._counters)},
        }
