"""Reviewer time-to-first-result: the score reframed into hours.

An artifact reviewer's first question is "how long until I see *something*
run". This estimates that from structural signals — a one-command path, a
container, data friction, a smoke target — and names the factors so the
author knows exactly what buys time back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import Evidence


@dataclass
class ReviewerTime:
    low_minutes: int
    high_minutes: int
    bucket: str
    factors: list[str] = field(default_factory=list)  # what is costing time
    unknown: bool = False

    @property
    def headline(self) -> str:
        if self.unknown:
            return "Reviewer time to first result: unknown (missing instructions)"
        return (
            f"Reviewer time to first result: {self.low_minutes}–{self.high_minutes} min ({self.bucket})"
        )


def _bucket(high: int) -> str:
    if high < 10:
        return "Excellent"
    if high <= 30:
        return "Good"
    if high <= 90:
        return "Risky"
    return "High reviewer burden"


def estimate(ev: Evidence) -> ReviewerTime:
    if not ev.docs.has_readme and not ev.env.has_runner:
        return ReviewerTime(0, 0, "Unknown", factors=["no README and no runnable entry surface"], unknown=True)

    low, high = 3, 8
    factors: list[str] = []

    def cost(low_add: int, high_add: int, reason: str) -> None:
        nonlocal low, high
        low += low_add
        high += high_add
        factors.append(reason)

    if not (ev.env.run_scripts or ev.env.makefile_targets or ev.manifest.smoke.command):
        cost(10, 30, "no one-command reproduction path")
    if not ev.env.has_container and not ev.env.has_conda_env:
        cost(5, 20, "environment must be assembled by hand (no container or conda env)")
    if not ev.deps.has_lockfile and ev.deps.pinned_fraction < 0.9:
        cost(5, 15, "dependency resolution may not converge to the original environment")
    if not ev.docs.has_section("install") and not ev.docs.has_readme:
        cost(10, 20, "no installation instructions")

    scripted_data = bool(ev.data.download_scripts) or ev.data.uses_dvc or ev.py.calls_any("datasets.load_dataset")
    documented_data = ev.data.dataset_urls or ev.docs.has_section("data") or bool(ev.manifest.datasets)
    if not scripted_data:
        if documented_data:
            cost(5, 20, "data must be fetched manually")
        elif ev.repo.frameworks.uses_any({"torch", "tensorflow", "sklearn", "jax", "transformers"}):
            cost(15, 45, "no documented way to obtain the data")
    if ev.remote.by_kind("gdrive"):
        cost(5, 15, "data behind Google Drive (throttling and permission prompts)")

    runtime_documented = ev.docs.has_section("hardware") or ev.docs.mentions_hardware_inline or ev.manifest.environment.hardware
    if not runtime_documented:
        cost(3, 10, "expected runtime and hardware not documented")
    if ev.manifest.smoke.command:
        low = max(1, low - 5)
        high = max(low + 2, high - 20)
    elif any("smoke" in c.path.lower() or "debug" in c.path.lower() or "tiny" in c.path.lower() for c in ev.config.files):
        high = max(low + 2, high - 10)
    else:
        factors.append("no smoke/quick-run target for a minutes-scale sanity check")
        high += 10

    if not (ev.docs.has_results_table or any(c.value is not None for c in ev.manifest.claims)):
        cost(2, 8, "expected results not stated (success is unrecognisable)")

    return ReviewerTime(low_minutes=low, high_minutes=high, bucket=_bucket(high), factors=factors)
