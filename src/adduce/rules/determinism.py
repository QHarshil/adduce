"""Determinism checks: will two runs of this code produce the same numbers?

Split across six rules so each knob is separately visible, suppressible, and
weighted: per-library seeds (R-DET-001), cuDNN flags (R-DET-002), strict
determinism (R-DET-003), the two DataLoader RNG sources (R-DET-004/005), and
sklearn ``random_state`` (R-DET-006). A PASS is always phrased as
"detected", never "guaranteed" — static analysis cannot confirm the calls
run before the first random draw.

R-DET-001, 002, 003 and 005 credit a setting only in code something outside
the tests references: a module, notebook or packaging entry point that calls,
imports or names it. A helper nothing references would otherwise score as
seeding; the findings name it instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..evidence import Evidence
from ..evidence.python_ast import AssignSite, CallSite
from ..model import Repo
from .base import Category, Finding, Location, Rule, Status

_RNG_FRAMEWORKS = frozenset({"torch", "tensorflow", "numpy", "sklearn", "jax", "lightning", "transformers"})

#: Framework-provided helpers that seed python, numpy, and torch in one call.
_UMBRELLA_SEEDERS = (
    "pytorch_lightning.seed_everything",
    "lightning.seed_everything",
    "lightning.pytorch.seed_everything",
    "lightning.fabric.seed_everything",
    "transformers.set_seed",
    "transformers.trainer_utils.set_seed",
    "accelerate.utils.set_seed",
)


#: Each RNG source R-DET-001 reports, with the calls that seed it.
_SEED_SOURCES: dict[str, tuple[str, ...]] = {
    "python (random.seed)": ("random.seed",),
    "numpy (np.random.seed or default_rng)": (
        "numpy.random.seed",
        "numpy.random.default_rng",
        "numpy.random.Generator",
    ),
    "torch (torch.manual_seed)": ("torch.manual_seed",),
    "torch CUDA (torch.cuda.manual_seed_all)": ("torch.cuda.manual_seed_all", "torch.cuda.manual_seed"),
    "tensorflow (tf.random.set_seed)": (
        "tensorflow.random.set_seed",
        "tf.random.set_seed",
        "keras.utils.set_random_seed",
    ),
    "jax (jax.random.PRNGKey)": ("jax.random.PRNGKey", "jax.random.key"),
}

#: The sources an umbrella seeder covers in one call.
_UMBRELLA_COVERS = frozenset(
    {
        "python (random.seed)",
        "numpy (np.random.seed or default_rng)",
        "torch (torch.manual_seed)",
        "torch CUDA (torch.cuda.manual_seed_all)",
    }
)

#: Without one of these the rule reports that nothing is seeded at all.
_DIRECT_SEEDS = (
    "random.seed",
    "numpy.random.seed",
    "numpy.random.default_rng",
    "numpy.random.Generator",
    "torch.manual_seed",
    *_UMBRELLA_SEEDERS,
)


_ADD_HELPER = (
    "Add a single set_all_seeds(seed) helper called at every entrypoint, covering "
    "random, numpy, and torch (CPU and CUDA). `adduce fix --scaffold seeds` generates one."
)


def _umbrella(ev: Evidence) -> bool:
    return ev.py.calls_referenced(*_UMBRELLA_SEEDERS)


def _unreferenced_holders(
    ev: Evidence, sites: Iterable[CallSite | AssignSite]
) -> dict[tuple[str, str], list[CallSite | AssignSite]]:
    """Group sites that do not count by the function that keeps them out, in source order."""
    holders: dict[tuple[str, str], list[CallSite | AssignSite]] = {}
    for site in sorted(sites, key=lambda s: (s.file, s.line)):
        function = ev.py.unreferenced_function(site)
        if function is not None:
            holders.setdefault((function, site.file), []).append(site)
    return holders


def _name_holders(holders: Iterable[tuple[str, str]]) -> str:
    # An empty name is the module scope of a test, which the file names.
    named = [f"{function} ({file})" if function else file for function, file in holders]
    if len(named) > 3:
        return ", ".join(named[:3]) + f", and {len(named) - 3} more"
    return ", ".join(named)


def _unreferenced_note(ev: Evidence, what: str, sites: Iterable[CallSite | AssignSite]) -> str:
    holders = _unreferenced_holders(ev, sites)
    if not holders:
        return ""
    return (
        f" {what} was found only in tests or in code adduce found no reference to outside the "
        "tests, which do not count: " + _name_holders(holders) + "."
    )


def _sources_seeded(sites: Iterable[CallSite | AssignSite]) -> set[str]:
    seeded: set[str] = set()
    for site in sites:
        if not isinstance(site, CallSite):
            continue
        if site.qualname in _UMBRELLA_SEEDERS:
            seeded |= _UMBRELLA_COVERS
        seeded |= {source for source, calls in _SEED_SOURCES.items() if site.qualname in calls}
    return seeded


def _enables_deterministic_algorithms(site: CallSite) -> bool:
    return site.first_literal is True or site.kw_value("mode") == "True"


class SeedDeterminismRule(Rule):
    id = "R-DET-001"
    category = Category.DETERMINISM
    title = "Random seeds set across all RNG sources"
    rationale = (
        "Unseeded random number generators are a leading cause of non-reproducible "
        "ML results. Each library keeps its own RNG state, so one seed call is not enough."
    )
    weight = 8
    fix_command = "adduce fix --scaffold seeds"

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses_any(_RNG_FRAMEWORKS)

    def evaluate(self, ev: Evidence) -> Finding:
        frameworks = ev.repo.frameworks
        umbrella = _umbrella(ev)

        # Only demand random.seed where the stdlib RNG is plausibly in play.
        python_rng_in_play = "random" in ev.py.imports or frameworks.uses_any(
            {"torch", "tensorflow", "jax", "lightning", "transformers"}
        )
        in_play = {
            "python (random.seed)": python_rng_in_play,
            "numpy (np.random.seed or default_rng)": frameworks.uses("numpy"),
            "torch (torch.manual_seed)": frameworks.uses("torch"),
            "torch CUDA (torch.cuda.manual_seed_all)": frameworks.uses("torch"),
            "tensorflow (tf.random.set_seed)": frameworks.uses("tensorflow"),
            "jax (jax.random.PRNGKey)": frameworks.uses("jax"),
        }
        missing = [
            source
            for source, calls in _SEED_SOURCES.items()
            if in_play[source]
            and not ev.py.calls_referenced(*calls)
            and not (umbrella and source in _UMBRELLA_COVERS)
        ]
        if not missing:
            return self.finding(
                Status.PASS,
                confidence=0.7,
                message="Seeding detected across all RNG sources in use "
                "(a detected signal, not a guarantee that seeding runs before every RNG draw).",
            )

        holders = _unreferenced_holders(
            ev,
            [
                site
                for name in (*_UMBRELLA_SEEDERS, *(c for calls in _SEED_SOURCES.values() for c in calls))
                for site in ev.py.call_sites(name)
            ],
        )
        any_seed_counts = umbrella or any(
            ev.py.calls_referenced(*calls) for calls in _SEED_SOURCES.values()
        )
        if holders and not any_seed_counts:
            return self._only_unreferenced(ev, holders, missing)

        missing_calls = {c for source in missing for c in _SEED_SOURCES[source]}
        note = _unreferenced_note(
            ev,
            "Seeding for a missing source",
            [site for name in sorted(missing_calls) for site in ev.py.call_sites(name)],
        )
        any_direct_seed = ev.py.calls_referenced(*_DIRECT_SEEDS)
        if not any_direct_seed:
            # With a note, seeding for a listed source was detected but does
            # not count, so "no seeding detected" would contradict it.
            lead = "No seeding that counts was detected" if note else "No seeding detected"
            return self.finding(
                Status.FAIL,
                confidence=0.9,
                message=lead + " for the RNG libraries this repository uses: "
                + ", ".join(missing) + "." + note,
                remediation=_ADD_HELPER,
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.85,
            message="Some RNG sources are seeded, but not all: missing " + ", ".join(missing) + "." + note,
            remediation="Extend the seeding helper to cover: " + ", ".join(missing) + ".",
        )

    def _only_unreferenced(
        self,
        ev: Evidence,
        holders: dict[tuple[str, str], list[CallSite | AssignSite]],
        missing: list[str],
    ) -> Finding:
        """Seeding only in tests or unreferenced helpers: reported as such, never as absent.

        The calls were detected, so "no seeding detected" would be false; the
        fix is to call the helper.
        """
        covers = {holder: _sources_seeded(sites) for holder, sites in holders.items()}
        # The helper to call is the one seeding the most sources. sorted() is
        # stable, so ties keep source order.
        ordered = sorted(holders, key=lambda holder: -len(covers[holder]))
        seeded_there = set().union(*covers.values())
        uncovered = [source for source in missing if source not in seeded_there]
        # A test fixture is not a helper to call from an entrypoint.
        callable_holders = [holder for holder in ordered if not ev.py.is_test_file(holder[1])]
        if callable_holders:
            function, file = callable_holders[0]
            remediation = (
                f"Import {function} from {file} and call it at the start of every entrypoint, "
                "before any model, dataset or DataLoader is built."
                + (" Then extend it to cover: " + ", ".join(uncovered) + "." if uncovered else "")
            )
        else:
            remediation = _ADD_HELPER
        return self.finding(
            Status.FAIL,
            # Lower than an outright absence: a shell command, ``getattr`` or a
            # computed import may reach the helper where this cannot see.
            confidence=0.7,
            message=(
                "Seeding calls were found only in tests or in code adduce found no reference to "
                "outside the tests: "
                + _name_holders(ordered)
                + ". Unseeded: "
                + ", ".join(missing)
                + "."
            ),
            remediation=remediation,
            locations=[Location(holders[h][0].file, holders[h][0].line) for h in ordered[:5]],
        )


class CudnnFlagsRule(Rule):
    id = "R-DET-002"
    category = Category.DETERMINISM
    title = "cuDNN determinism flags set"
    rationale = (
        "cuDNN selects convolution algorithms at runtime; without deterministic=True and "
        "benchmark=False, the same seeded run can produce different numbers on the same GPU."
    )
    weight = 4
    fix_command = "adduce fix --scaffold seeds"

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch") or repo.frameworks.uses("lightning")

    def evaluate(self, ev: Evidence) -> Finding:
        deterministic = ev.py.assigns_referenced("torch.backends.cudnn.deterministic", True)
        benchmark_off = ev.py.assigns_referenced("torch.backends.cudnn.benchmark", False)
        strict = any(
            _enables_deterministic_algorithms(site)
            for site in ev.py.referenced_call_sites("torch.use_deterministic_algorithms")
        )
        lightning_deterministic = any(
            site.kw_value("deterministic") == "True"
            for site in ev.py.referenced_call_sites_terminal("Trainer")
        )
        if (deterministic and benchmark_off) or strict or lightning_deterministic:
            via = "torch.use_deterministic_algorithms" if strict and not deterministic else (
                "Trainer(deterministic=...)" if lightning_deterministic and not deterministic
                else "cudnn.deterministic=True, cudnn.benchmark=False"
            )
            return self.finding(Status.PASS, confidence=0.75, message=f"cuDNN determinism controlled via {via} (detected).")
        unset = [
            site
            for site in ev.py.assign_sites("torch.backends.cudnn.deterministic")
            if site.value is True and not deterministic
        ] + [
            site
            for site in ev.py.assign_sites("torch.backends.cudnn.benchmark")
            if site.value is False and not benchmark_off
        ]
        note = _unreferenced_note(ev, "A missing cuDNN flag", unset)
        if deterministic or benchmark_off:
            missing = "torch.backends.cudnn.benchmark = False" if deterministic else "torch.backends.cudnn.deterministic = True"
            return self.finding(
                Status.PARTIAL,
                confidence=0.8,
                message=f"One cuDNN flag is set but not the other; missing {missing}." + note,
                remediation=f"Add {missing} next to the existing flag.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="Neither torch.backends.cudnn.deterministic=True nor benchmark=False detected." + note,
            remediation="Set torch.backends.cudnn.deterministic = True and torch.backends.cudnn.benchmark = False in the seeding helper.",
        )


class StrictDeterminismRule(Rule):
    id = "R-DET-003"
    category = Category.DETERMINISM
    title = "Strict determinism controls (deterministic algorithms, hash seed, CUBLAS workspace)"
    rationale = (
        "Deterministic-algorithm mode plus process-start hash and CUBLAS settings reduce known "
        "nondeterminism beyond ordinary RNG seeds; they do not prove bit-exact reproduction."
    )
    weight = 2

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        enabling = [
            site
            for site in ev.py.call_sites("torch.use_deterministic_algorithms")
            if _enables_deterministic_algorithms(site)
        ]
        deterministic_algorithms = any(ev.py.in_referenced_code(site) for site in enabling)
        controls = {
            "torch.use_deterministic_algorithms(True)": deterministic_algorithms,
            "PYTHONHASHSEED at interpreter startup": ev.env.sets_at_process_start(
                "PYTHONHASHSEED"
            ),
            "CUBLAS_WORKSPACE_CONFIG at process startup": ev.env.sets_at_process_start(
                "CUBLAS_WORKSPACE_CONFIG"
            ),
        }
        present = [name for name, ok in controls.items() if ok]
        missing = [name for name, ok in controls.items() if not ok]
        ignored_runtime = [
            name
            for name in ("PYTHONHASHSEED", "CUBLAS_WORKSPACE_CONFIG")
            if ev.py.sets_env(name) and not ev.env.sets_at_process_start(name)
        ]
        runtime_note = (
            " Runtime-only assignment does not establish startup configuration: "
            + ", ".join(ignored_runtime)
            + "."
            if ignored_runtime
            else ""
        )
        if not deterministic_algorithms:
            runtime_note += _unreferenced_note(ev, "torch.use_deterministic_algorithms(True)", enabling)
        if len(present) == 3:
            return self.finding(
                Status.PASS,
                confidence=0.75,
                message=(
                    "All three strict determinism controls were detected; this reduces known "
                    "nondeterminism but does not prove bit-exact results."
                ),
            )
        if present:
            return self.finding(
                Status.PARTIAL,
                confidence=0.7,
                message=(
                    "Some strict controls were detected ("
                    + ", ".join(present)
                    + "); missing "
                    + ", ".join(missing)
                    + "."
                    + runtime_note
                ),
                remediation=(
                    "Set process environment controls in the launcher or container before Python "
                    "starts, and enable deterministic algorithms in code."
                ),
            )
        return self.finding(
            Status.FAIL,
            confidence=0.7,
            message=(
                "No strict determinism controls were detected ("
                + ", ".join(missing)
                + ")."
                + runtime_note
            ),
            remediation=(
                "Set PYTHONHASHSEED and CUBLAS_WORKSPACE_CONFIG in the launcher or container before "
                "Python starts, then call torch.use_deterministic_algorithms(True). Verify the "
                "specific operations and platform empirically."
            ),
        )


class DataLoaderGeneratorRule(Rule):
    id = "R-DET-004"
    category = Category.DETERMINISM
    title = "Shuffling DataLoaders use a seeded generator"
    rationale = (
        "A shuffling DataLoader without an explicit generator= draws sample order from global "
        "RNG state, which changes whenever anything else consumes that state."
    )
    weight = 4
    fix_command = "adduce fix --scaffold seeds"

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        if not ev.py.dataloaders:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No torch DataLoader construction detected.")
        shuffling = [s for s in ev.py.dataloaders if s.shuffle is True and not s.has_sampler]
        if not shuffling:
            return self.finding(Status.NOT_APPLICABLE, confidence=0.6, message="No shuffling DataLoader detected.")
        gaps = [s for s in shuffling if not s.has_generator]
        if not gaps:
            return self.finding(
                Status.PASS,
                confidence=0.75,
                message=f"All {len(shuffling)} shuffling DataLoader site(s) pass an explicit generator=.",
            )
        return self.finding(
            Status.PARTIAL if len(gaps) < len(shuffling) else Status.FAIL,
            confidence=0.8,
            message=f"{len(gaps)} of {len(shuffling)} shuffling DataLoader site(s) lack a seeded generator=.",
            remediation="Pass generator=torch.Generator().manual_seed(seed) to each shuffling DataLoader.",
            locations=[Location(g.file, g.line) for g in gaps],
        )


class DataLoaderWorkerRule(Rule):
    id = "R-DET-005"
    category = Category.DETERMINISM
    title = "Multi-worker DataLoaders reseed worker RNGs"
    rationale = (
        "DataLoader workers receive distinct PyTorch seeds, while other libraries and version-"
        "specific worker behavior can require explicit initialization. A worker_init_fn makes "
        "third-party RNG policy visible and derived from the worker seed."
    )
    weight = 3
    fix_command = "adduce fix --scaffold seeds"

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("torch")

    def evaluate(self, ev: Evidence) -> Finding:
        multi_worker = [s for s in ev.py.dataloaders if (s.num_workers or 0) > 0]
        if not multi_worker:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No DataLoader with num_workers > 0 detected."
            )
        # lightning's seed_everything(workers=True) installs the worker seeder globally.
        umbrella_workers = any(
            site.kw_value("workers") == "True"
            for name in ("pytorch_lightning.seed_everything", "lightning.seed_everything")
            for site in ev.py.referenced_call_sites(name)
        )
        gaps = [s for s in multi_worker if not s.has_worker_init_fn]
        if umbrella_workers or not gaps:
            via = " (via seed_everything(workers=True))" if umbrella_workers and gaps else ""
            return self.finding(
                Status.PASS,
                confidence=0.75,
                message=f"Worker RNG seeding covered at all {len(multi_worker)} multi-worker DataLoader site(s){via}.",
            )
        return self.finding(
            Status.PARTIAL,
            confidence=0.7,
            message=f"{len(gaps)} of {len(multi_worker)} multi-worker DataLoader site(s) lack worker_init_fn.",
            remediation=(
                "Pass a worker_init_fn that reseeds numpy and random from torch.initial_seed(), or use "
                "lightning's seed_everything(seed, workers=True)."
            ),
            locations=[Location(g.file, g.line) for g in gaps],
        )


class SklearnRandomStateRule(Rule):
    id = "R-DET-006"
    category = Category.DETERMINISM
    title = "random_state set on scikit-learn estimators and splitters"
    rationale = (
        "When random_state is omitted, stochastic scikit-learn calls can depend on mutable global "
        "RNG state and call order. An explicit value isolates each call's randomness."
    )
    weight = 4

    def applies_to(self, repo: Repo) -> bool:
        return repo.frameworks.uses("sklearn")

    def evaluate(self, ev: Evidence) -> Finding:
        estimators = ev.py.estimators
        if not estimators:
            return self.finding(
                Status.NOT_APPLICABLE, confidence=0.6, message="No calls to stochastic scikit-learn APIs detected."
            )
        unseeded = ev.py.unseeded_estimators()
        if not unseeded:
            return self.finding(
                Status.PASS,
                confidence=0.8,
                message=f"random_state is set at all {len(estimators)} detected stochastic sklearn call site(s).",
            )
        names = sorted({e.qualname.rsplit(".", 1)[-1] for e in unseeded})
        return self.finding(
            Status.FAIL if len(unseeded) == len(estimators) else Status.PARTIAL,
            confidence=0.85,
            message=f"{len(unseeded)} of {len(estimators)} stochastic sklearn call site(s) omit random_state: "
            + ", ".join(names) + ".",
            remediation="Pass random_state=<seed> at each flagged call site.",
            locations=[Location(e.file, e.line) for e in unseeded],
        )
