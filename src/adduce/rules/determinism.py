"""Determinism checks: will two runs of this code produce the same numbers?

Split across six rules so each knob is separately visible, suppressible, and
weighted: per-library seeds (R-DET-001), cuDNN flags (R-DET-002), strict
determinism (R-DET-003), the two DataLoader RNG sources (R-DET-004/005), and
sklearn ``random_state`` (R-DET-006). A PASS is always phrased as
"detected", never "guaranteed" — static analysis cannot confirm the calls
run before the first random draw.
"""

from __future__ import annotations

from ..evidence import Evidence
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


def _umbrella(ev: Evidence) -> bool:
    return ev.py.calls_any(*_UMBRELLA_SEEDERS)


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
        seeded = {
            "python (random.seed)": ev.py.calls("random.seed") or umbrella or not python_rng_in_play,
            "numpy (np.random.seed or default_rng)": (
                ev.py.calls("numpy.random.seed")
                or ev.py.uses_numpy_generator
                or umbrella
                or not frameworks.uses("numpy")
            ),
            "torch (torch.manual_seed)": (
                ev.py.calls("torch.manual_seed") or umbrella or not frameworks.uses("torch")
            ),
            "torch CUDA (torch.cuda.manual_seed_all)": (
                ev.py.calls_any("torch.cuda.manual_seed_all", "torch.cuda.manual_seed")
                or umbrella
                or not frameworks.uses("torch")
            ),
            "tensorflow (tf.random.set_seed)": (
                ev.py.calls_any(
                    "tensorflow.random.set_seed", "tf.random.set_seed", "keras.utils.set_random_seed"
                )
                or not frameworks.uses("tensorflow")
            ),
            "jax (jax.random.PRNGKey)": (
                ev.py.calls_any("jax.random.PRNGKey", "jax.random.key") or not frameworks.uses("jax")
            ),
        }
        missing = [name for name, ok in seeded.items() if not ok]

        any_direct_seed = any(
            [
                ev.py.calls("random.seed"),
                ev.py.calls("numpy.random.seed"),
                ev.py.uses_numpy_generator,
                ev.py.calls("torch.manual_seed"),
                umbrella,
            ]
        )
        if missing and not any_direct_seed:
            return self.finding(
                Status.FAIL,
                confidence=0.9,
                message="No seeding detected for the RNG libraries this repository uses: "
                + ", ".join(missing) + ".",
                remediation=(
                    "Add a single set_all_seeds(seed) helper called at every entrypoint, covering "
                    "random, numpy, and torch (CPU and CUDA). `adduce fix --scaffold seeds` generates one."
                ),
            )
        if missing:
            return self.finding(
                Status.PARTIAL,
                confidence=0.85,
                message="Some RNG sources are seeded, but not all: missing " + ", ".join(missing) + ".",
                remediation="Extend the seeding helper to cover: " + ", ".join(missing) + ".",
            )
        return self.finding(
            Status.PASS,
            confidence=0.7,
            message="Seeding detected across all RNG sources in use "
            "(a detected signal, not a guarantee that seeding runs before every RNG draw).",
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
        deterministic = ev.py.assigns("torch.backends.cudnn.deterministic", True)
        benchmark_off = ev.py.assigns("torch.backends.cudnn.benchmark", False)
        strict = any(
            site.first_literal is True or site.kw_value("mode") == "True"
            for site in ev.py.call_sites("torch.use_deterministic_algorithms")
        )
        lightning_deterministic = any(
            site.kw_value("deterministic") == "True"
            for site in ev.py.call_sites_terminal("Trainer")
        )
        if (deterministic and benchmark_off) or strict or lightning_deterministic:
            via = "torch.use_deterministic_algorithms" if strict and not deterministic else (
                "Trainer(deterministic=...)" if lightning_deterministic and not deterministic
                else "cudnn.deterministic=True, cudnn.benchmark=False"
            )
            return self.finding(Status.PASS, confidence=0.75, message=f"cuDNN determinism controlled via {via} (detected).")
        if deterministic or benchmark_off:
            missing = "torch.backends.cudnn.benchmark = False" if deterministic else "torch.backends.cudnn.deterministic = True"
            return self.finding(
                Status.PARTIAL,
                confidence=0.8,
                message=f"One cuDNN flag is set but not the other; missing {missing}.",
                remediation=f"Add {missing} next to the existing flag.",
            )
        return self.finding(
            Status.FAIL,
            confidence=0.8,
            message="Neither torch.backends.cudnn.deterministic=True nor benchmark=False detected.",
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
        deterministic_algorithms = any(
            site.first_literal is True or site.kw_value("mode") == "True"
            for site in ev.py.call_sites("torch.use_deterministic_algorithms")
        )
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
            for site in ev.py.call_sites(name)
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
