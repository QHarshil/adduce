"""The determinism rules: layered grading, framework gating, false-positive control."""

from __future__ import annotations

from pathlib import Path

import pytest

from adduce.engine import run_check
from adduce.rules.base import Status
from adduce.rules.determinism import (
    CudnnFlagsRule,
    DataLoaderGeneratorRule,
    DataLoaderWorkerRule,
    SeedDeterminismRule,
    SklearnRandomStateRule,
    StrictDeterminismRule,
)

_TORCH_REQS = {"requirements.txt": "torch==2.1.0\nnumpy==1.26.0\n"}

_FULL_SEEDING = (
    "import os\nimport random\nimport numpy as np\nimport torch\n"
    "random.seed(0)\nnp.random.seed(0)\ntorch.manual_seed(0)\n"
    "torch.cuda.manual_seed_all(0)\n"
)


def test_unseeded_torch_repo_fails(make_evidence):
    ev = make_evidence(
        {**_TORCH_REQS, "train.py": "import torch\nimport numpy as np\nmodel = torch.nn.Linear(2, 2)\n"}
    )
    rule = SeedDeterminismRule()
    assert rule.applies_to(ev.repo)
    assert rule.evaluate(ev).status is Status.FAIL


def test_partial_seeding_is_partial(make_evidence):
    ev = make_evidence(
        {**_TORCH_REQS, "train.py": "import torch\nimport numpy as np\ntorch.manual_seed(0)\n"}
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "numpy" in finding.message


def test_comprehensive_seeding_passes_with_honest_wording(make_evidence):
    ev = make_evidence({**_TORCH_REQS, "train.py": _FULL_SEEDING})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PASS
    assert "signal" in finding.message or "guarantee" in finding.message


def test_lightning_seed_everything_covers_core(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "pytorch-lightning==2.0.0\ntorch==2.1.0\n",
            "train.py": "import pytorch_lightning as pl\npl.seed_everything(0, workers=True)\n",
        }
    )
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


def test_sklearn_only_repo_not_nagged_about_cuda(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "scikit-learn==1.4.0\nnumpy==1.26.0\n",
            "model.py": (
                "import numpy as np\nfrom sklearn.ensemble import RandomForestClassifier\n"
                "np.random.seed(0)\nclf = RandomForestClassifier(random_state=0)\n"
            ),
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PASS
    assert "cudnn" not in finding.message.lower()
    # And the CUDA-specific rules never even apply.
    assert not CudnnFlagsRule().applies_to(ev.repo)
    assert not StrictDeterminismRule().applies_to(ev.repo)


def test_seed_rule_not_applicable_without_rng_frameworks(make_repo):
    repo = make_repo({"tool.py": "print('no ml here')\n"})
    assert not SeedDeterminismRule().applies_to(repo)


def test_cudnn_flags_graded(make_evidence):
    both = make_evidence(
        {**_TORCH_REQS, "a.py": "import torch\ntorch.backends.cudnn.deterministic = True\ntorch.backends.cudnn.benchmark = False\n"}
    )
    assert CudnnFlagsRule().evaluate(both).status is Status.PASS
    one = make_evidence(
        {**_TORCH_REQS, "a.py": "import torch\ntorch.backends.cudnn.deterministic = True\n"}
    )
    assert CudnnFlagsRule().evaluate(one).status is Status.PARTIAL
    none = make_evidence({**_TORCH_REQS, "a.py": "import torch\n"})
    assert CudnnFlagsRule().evaluate(none).status is Status.FAIL


def test_use_deterministic_algorithms_satisfies_cudnn_rule(make_evidence):
    ev = make_evidence(
        {**_TORCH_REQS, "a.py": "import torch\ntorch.use_deterministic_algorithms(True)\n"}
    )
    assert CudnnFlagsRule().evaluate(ev).status is Status.PASS


def test_strict_determinism_counts_controls(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "a.py": (
                "import os\nimport torch\n"
                "os.environ['PYTHONHASHSEED'] = '0'\n"
                "torch.use_deterministic_algorithms(True)\n"
            ),
        }
    )
    finding = StrictDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "PYTHONHASHSEED at interpreter startup" in finding.message
    assert "Runtime-only assignment" in finding.message


def test_strict_determinism_accepts_only_enabled_mode_and_startup_environment(
    make_evidence,
):
    disabled = make_evidence(
        {
            **_TORCH_REQS,
            "a.py": "import torch\ntorch.use_deterministic_algorithms(False)\n",
            "Dockerfile": (
                "FROM python:3.12\n"
                "ENV PYTHONHASHSEED=0 CUBLAS_WORKSPACE_CONFIG=:4096:8\n"
            ),
        }
    )
    assert StrictDeterminismRule().evaluate(disabled).status is Status.PARTIAL
    assert CudnnFlagsRule().evaluate(disabled).status is Status.FAIL

    enabled_single_line = make_evidence(
        {
            **_TORCH_REQS,
            "a.py": "import torch\ntorch.use_deterministic_algorithms(True)\n",
            "Dockerfile": (
                "FROM python:3.12\n"
                "ENV PYTHONHASHSEED=0 CUBLAS_WORKSPACE_CONFIG=:4096:8\n"
            ),
        }
    )
    assert StrictDeterminismRule().evaluate(enabled_single_line).status is Status.PASS

    enabled = make_evidence(
        {
            **_TORCH_REQS,
            "a.py": "import torch\ntorch.use_deterministic_algorithms(mode=True)\n",
            "Dockerfile": (
                "FROM python:3.12\n"
                "ENV PYTHONHASHSEED=0 \\\n"
                "    CUBLAS_WORKSPACE_CONFIG=:4096:8\n"
            ),
        }
    )
    finding = StrictDeterminismRule().evaluate(enabled)
    assert finding.status is Status.PASS
    assert "does not prove bit-exact" in finding.message


def test_lightning_deterministic_false_does_not_satisfy_cudnn_rule(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "a.py": "from lightning import Trainer\nTrainer(deterministic=False)\n",
        }
    )

    assert CudnnFlagsRule().evaluate(ev).status is Status.FAIL


def test_dataloader_generator_rule(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "data.py": (
                "import torch\nfrom torch.utils.data import DataLoader\n"
                "bad = DataLoader(ds, shuffle=True)\n"
                "good = DataLoader(ds, shuffle=True, generator=g)\n"
            ),
        }
    )
    finding = DataLoaderGeneratorRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert finding.locations[0].line == 3


def test_dataloader_worker_rule(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "data.py": (
                "import torch\nfrom torch.utils.data import DataLoader\n"
                "loader = DataLoader(ds, num_workers=4)\n"
            ),
        }
    )
    finding = DataLoaderWorkerRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert finding.locations[0].path == "data.py"


def test_lightning_workers_false_does_not_satisfy_worker_seeding(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "lightning==2.0.0\ntorch==2.1.0\n",
            "train.py": (
                "import torch\n"
                "from lightning import seed_everything\n"
                "from torch.utils.data import DataLoader\n"
                "seed_everything(0, workers=False)\n"
                "loader = DataLoader(None, num_workers=2)\n"
            ),
        }
    )

    assert DataLoaderWorkerRule().evaluate(ev).status is Status.PARTIAL


def test_dataloader_rules_na_without_loaders(make_evidence):
    ev = make_evidence({**_TORCH_REQS, "train.py": "import torch\n"})
    assert DataLoaderGeneratorRule().evaluate(ev).status is Status.NOT_APPLICABLE
    assert DataLoaderWorkerRule().evaluate(ev).status is Status.NOT_APPLICABLE


def test_sklearn_rule_partial_when_mixed(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "scikit-learn==1.4.0\n",
            "model.py": (
                "from sklearn.cluster import KMeans\n"
                "from sklearn.model_selection import train_test_split\n"
                "KMeans(n_clusters=3, random_state=0)\n"
                "train_test_split(X, y)\n"
            ),
        }
    )
    finding = SklearnRandomStateRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "train_test_split" in finding.message


# -- seeding that nothing outside the tests references ---------------------
#
# A helper nothing calls, imports or names does not seed a run. It earns no
# credit, and the finding names it rather than claiming seeding is absent.

_NUMPY_REQS = {"requirements.txt": "numpy==1.26.0\n"}

_ORPHAN = (
    "import random\n"
    "import numpy as np\n\n"
    "def set_seed(seed):\n"
    "    random.seed(seed)\n"
    "    np.random.seed(seed)\n"
)

_TRAIN = (
    "import numpy as np\n\n"
    "def main():\n"
    "    return np.random.permutation(10)\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def test_an_orphan_seed_helper_fails_and_is_named(make_evidence):
    ev = make_evidence({**_NUMPY_REQS, "seeding.py": _ORPHAN, "train.py": _TRAIN})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert finding.confidence == 0.7
    assert "No seeding detected" not in finding.message
    # States what adduce detected, not what the repository contains.
    assert finding.message.startswith(
        "Seeding calls were found only in tests or in code adduce found no reference to outside "
        "the tests: set_seed (seeding.py). Unseeded: "
    )
    assert finding.remediation.startswith("Import set_seed from seeding.py and call it")
    assert [(loc.path, loc.line) for loc in finding.locations] == [("seeding.py", 5)]


def test_calling_the_helper_from_the_entrypoint_restores_credit(make_evidence):
    # Control: green on base, where every seeding call counted.
    train = "from seeding import set_seed\n" + _TRAIN.replace("    return", "    set_seed(0)\n    return")
    ev = make_evidence({**_NUMPY_REQS, "seeding.py": _ORPHAN, "train.py": train})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


@pytest.mark.parametrize(
    "zoo, name",
    [
        pytest.param(
            "import random\nimport numpy as np\nfrom ._registry import register_model\n\n"
            "@register_model\ndef resnet_seeded():\n    random.seed(0)\n    np.random.seed(0)\n",
            "resnet_seeded",
            id="function",
        ),
        pytest.param(
            "import random\nimport numpy as np\nfrom ._registry import register_model\n\n"
            "@register_model\nclass ResNetSeeded:\n    def setup(self):\n"
            "        random.seed(0)\n        np.random.seed(0)\n",
            "ResNetSeeded",
            id="class",
        ),
    ],
)
def test_a_helper_a_bare_decorator_registers_is_not_reported(make_evidence, zoo, name):
    # Control: green on base, where every seeding call counted. Pins that a
    # decorator written without a call still credits what it registers.
    ev = make_evidence({**_NUMPY_REQS, "models/zoo.py": zoo})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PASS
    assert name not in finding.message


def test_a_helper_only_a_test_calls_earns_no_credit(make_evidence):
    test = "from seeding import set_seed\n\ndef test_seed():\n    set_seed(0)\n"
    ev = make_evidence(
        {**_NUMPY_REQS, "seeding.py": _ORPHAN, "train.py": _TRAIN, "tests/test_seeding.py": test}
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "set_seed (seeding.py)" in finding.message


def test_a_helper_called_from_a_method_of_an_instantiated_class_earns_credit(make_evidence):
    # Control: green on base, where every seeding call counted.
    trainer = (
        "from seeding import set_seed\n\n"
        "class Trainer:\n"
        "    def __init__(self, seed):\n"
        "        set_seed(seed)\n"
    )
    train = "from trainer import Trainer\n\nif __name__ == '__main__':\n    Trainer(0)\n"
    ev = make_evidence({**_NUMPY_REQS, "seeding.py": _ORPHAN, "trainer.py": trainer, "train.py": train})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


def test_seeding_only_in_a_test_fixture_is_named_but_never_recommended(make_evidence):
    conftest = (
        "import random as rand\nimport numpy\nimport pytest\n\n"
        "@pytest.fixture\n"
        "def random():\n"
        "    rand.seed(42)\n"
        "    numpy.random.seed(42)\n"
    )
    # ``np.random`` names the fixture by attribute, which gives a test no credit.
    train = "import numpy as np\n\nif __name__ == '__main__':\n    print(np.random.rand(3))\n"
    ev = make_evidence({**_NUMPY_REQS, "train.py": train, "tests/conftest.py": conftest})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "random (tests/conftest.py)" in finding.message
    assert finding.remediation.startswith("Add a single set_all_seeds(seed) helper")


def test_the_helper_seeding_the_most_sources_is_the_one_recommended(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "a_worker.py": "import numpy as np\n\ndef seed_worker(i):\n    np.random.seed(i)\n",
            "b_seeding.py": (
                "import random\nimport numpy as np\nimport torch\n\n"
                "def seed_all(seed):\n"
                "    random.seed(seed)\n"
                "    np.random.seed(seed)\n"
                "    torch.manual_seed(seed)\n"
            ),
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "seed_all (b_seeding.py), seed_worker (a_worker.py)" in finding.message
    assert finding.remediation.startswith("Import seed_all from b_seeding.py")
    assert finding.remediation.endswith("Then extend it to cover: torch CUDA (torch.cuda.manual_seed_all).")


def test_more_than_three_helpers_are_counted_not_listed(make_evidence):
    files = {
        f"seed_{i}.py": f"import numpy as np\n\ndef seed_{i}():\n    np.random.seed({i})\n" for i in range(5)
    }
    ev = make_evidence({**_NUMPY_REQS, **files})
    finding = SeedDeterminismRule().evaluate(ev)
    assert "seed_2 (seed_2.py), and 2 more." in finding.message
    assert len(finding.locations) == 5


def test_an_unreferenced_umbrella_seeder_earns_no_credit(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "pytorch-lightning==2.0.0\ntorch==2.1.0\nnumpy==1.26.0\n",
            "seeding.py": "import pytorch_lightning as pl\n\ndef seed(s):\n    pl.seed_everything(s)\n",
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "seed (seeding.py)" in finding.message
    # The umbrella covers every source this repository needs.
    assert "Then extend" not in finding.remediation


def test_partial_seeding_names_the_helper_holding_the_missing_source(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "train.py": "import random\nimport numpy as np\nrandom.seed(0)\nnp.random.seed(0)\n",
            "seeding.py": (
                "import torch\n\n"
                "def seed_torch(s):\n"
                "    torch.manual_seed(s)\n"
                "    torch.cuda.manual_seed_all(s)\n"
            ),
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "missing torch (torch.manual_seed)" in finding.message
    assert (
        " Seeding for a missing source was found only in tests or in code adduce found no "
        "reference to outside the tests, which do not count: seed_torch (seeding.py)."
    ) in finding.message


def test_no_direct_seed_still_names_the_helper_holding_a_missing_source(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "tensorflow==2.15.0\nnumpy==1.26.0\n",
            "train.py": "import tensorflow as tf\ntf.random.set_seed(0)\n",
            "seeding.py": "import numpy as np\n\ndef seed_np(s):\n    np.random.seed(s)\n",
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert finding.message.startswith("No seeding that counts was detected")
    assert "seed_np (seeding.py)" in finding.message


_FLAGS_HELPER = (
    "import torch\n\n"
    "def make_deterministic():\n"
    "    torch.backends.cudnn.deterministic = True\n"
    "    torch.backends.cudnn.benchmark = False\n"
)


def test_cudnn_flags_in_an_unreferenced_helper_do_not_count(make_evidence):
    ev = make_evidence({**_TORCH_REQS, "seeding.py": _FLAGS_HELPER})
    finding = CudnnFlagsRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "make_deterministic (seeding.py)" in finding.message
    called = make_evidence({**_TORCH_REQS, "seeding.py": _FLAGS_HELPER + "\nmake_deterministic()\n"})
    assert CudnnFlagsRule().evaluate(called).status is Status.PASS


def test_one_cudnn_flag_in_an_unreferenced_helper_is_named_in_the_partial(make_evidence):
    ev = make_evidence(
        {
            **_TORCH_REQS,
            "train.py": "import torch\ntorch.backends.cudnn.deterministic = True\n",
            "seeding.py": "import torch\n\ndef no_bench():\n    torch.backends.cudnn.benchmark = False\n",
        }
    )
    finding = CudnnFlagsRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "no_bench (seeding.py)" in finding.message


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("    torch.use_deterministic_algorithms(True)\n", id="deterministic-algorithms"),
        pytest.param("    Trainer(deterministic=True)\n", id="lightning-trainer"),
    ],
)
def test_other_cudnn_controls_in_an_unreferenced_helper_do_not_count(make_evidence, body):
    # ``model.py`` is not an entrypoint name, where every function would count.
    source = "import torch\nfrom lightning import Trainer\n\ndef build():\n" + body
    ev = make_evidence({**_TORCH_REQS, "model.py": source})
    assert CudnnFlagsRule().evaluate(ev).status is Status.FAIL
    called = make_evidence({**_TORCH_REQS, "model.py": source + "\nbuild()\n"})
    assert CudnnFlagsRule().evaluate(called).status is Status.PASS


def test_strict_mode_in_an_unreferenced_helper_does_not_count(make_evidence):
    source = "import torch\n\ndef strict():\n    torch.use_deterministic_algorithms(True)\n"
    ev = make_evidence({**_TORCH_REQS, "seeding.py": source})
    finding = StrictDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "strict (seeding.py)" in finding.message
    called = make_evidence({**_TORCH_REQS, "seeding.py": source + "\nstrict()\n"})
    assert StrictDeterminismRule().evaluate(called).status is Status.PARTIAL


def test_worker_seeding_in_an_unreferenced_helper_does_not_count(make_evidence):
    source = (
        "import torch\n"
        "from lightning import seed_everything\n"
        "from torch.utils.data import DataLoader\n\n"
        "def seed():\n"
        "    seed_everything(0, workers=True)\n\n"
        "loader = DataLoader(None, num_workers=2)\n"
    )
    files = {"requirements.txt": "lightning==2.0.0\ntorch==2.1.0\n"}
    ev = make_evidence({**files, "model.py": source})
    assert DataLoaderWorkerRule().evaluate(ev).status is Status.PARTIAL
    called = make_evidence({**files, "model.py": source + "seed()\n"})
    assert DataLoaderWorkerRule().evaluate(called).status is Status.PASS


def test_the_orphan_helper_synthetic_case_names_its_helper():
    case = Path(__file__).resolve().parent.parent / "corpus" / "synthetic" / "synthetic_orphan_seed_helper"
    finding = next(f for f in run_check(case).card.findings if f.rule_id == "R-DET-001")
    assert finding.status is Status.FAIL
    assert "set_seed (seeding.py)" in finding.message
    assert "No seeding detected" not in finding.message


# -- the seeding sources each count on their own ---------------------------


@pytest.mark.parametrize("call", ["np.random.default_rng(0)", "np.random.Generator(np.random.PCG64(0))"])
def test_a_numpy_generator_alone_seeds_numpy(make_evidence, call):
    # Control: green on base. Pins each Generator alternative.
    ev = make_evidence({**_NUMPY_REQS, "model.py": f"import numpy as np\nrng = {call}\n"})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


@pytest.mark.parametrize("call", ["np.random.default_rng(0)", "np.random.Generator(np.random.PCG64(0))"])
def test_a_numpy_generator_alone_is_seeding_detected(make_evidence, call):
    # Control: green on base. Pins each Generator alternative as a direct seed.
    ev = make_evidence({**_TORCH_REQS, "model.py": f"import numpy as np\nimport torch\nrng = {call}\n"})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "missing python (random.seed), torch (torch.manual_seed)" in finding.message


@pytest.mark.parametrize(
    "imports, call",
    [
        pytest.param("import pytorch_lightning as pl", "pl.seed_everything(0)", id="pytorch-lightning"),
        pytest.param("import lightning as L", "L.seed_everything(0)", id="lightning"),
        pytest.param("from lightning.pytorch import seed_everything", "seed_everything(0)", id="lightning-pytorch"),
        pytest.param("from lightning.fabric import seed_everything", "seed_everything(0)", id="lightning-fabric"),
        pytest.param("from transformers import set_seed", "set_seed(0)", id="transformers"),
        pytest.param("from transformers.trainer_utils import set_seed", "set_seed(0)", id="trainer-utils"),
        pytest.param("from accelerate.utils import set_seed", "set_seed(0)", id="accelerate"),
    ],
)
def test_an_umbrella_seeder_alone_seeds_the_core_sources(make_evidence, imports, call):
    # Control: green on base. Pins each umbrella seeder.
    ev = make_evidence({**_TORCH_REQS, "model.py": f"import torch\n{imports}\n{call}\n"})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


_JAX_UMBRELLA = {"requirements.txt": "torch==2.1.0\nnumpy==1.26.0\njax==0.4.20\n"}


def test_an_umbrella_seeder_alone_is_seeding_detected(make_evidence):
    # Control: green on base. Pins the umbrella seeders as direct seeds.
    source = "import jax\nimport torch\nfrom transformers import set_seed\nset_seed(0)\n"
    ev = make_evidence({**_JAX_UMBRELLA, "model.py": source})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert finding.message.startswith("Some RNG sources are seeded, but not all: missing jax")


def test_an_umbrella_seeder_counts_beside_an_unreferenced_helper(make_evidence):
    ev = make_evidence(
        {
            **_JAX_UMBRELLA,
            "model.py": "import torch\nfrom transformers import set_seed\nset_seed(0)\n",
            "keys.py": "import jax\n\ndef key():\n    return jax.random.PRNGKey(0)\n",
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "key (keys.py)" in finding.message


# -- code something outside the tests references ---------------------------


def test_seeding_in_the_init_of_a_class_used_at_module_scope_counts(make_evidence):
    # Control: green on base, where every seeding call counted.
    source = (
        "import random\nimport numpy as np\n\n"
        "class Experiment:\n"
        "    def __init__(self, seed):\n"
        "        random.seed(seed)\n"
        "        np.random.seed(seed)\n\n"
        "    def go(self):\n"
        "        return np.random.permutation(10)\n\n"
        "print(Experiment(0).go())\n"
    )
    ev = make_evidence({**_NUMPY_REQS, "sweep.py": source})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


def test_a_class_nothing_names_is_what_the_finding_recommends(make_evidence):
    source = "import numpy as np\n\nclass Experiment:\n    def __init__(self):\n        np.random.seed(0)\n"
    ev = make_evidence({**_NUMPY_REQS, "sweep.py": source})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert finding.remediation.startswith("Import Experiment from sweep.py")


def test_module_scope_seeding_in_a_test_earns_no_credit(make_evidence):
    test = "import random\nimport numpy as np\nimport pytest\n\nrandom.seed(0)\nnp.random.seed(0)\n"
    train = "import numpy as np\n\nif __name__ == '__main__':\n    print(np.random.permutation(10))\n"
    ev = make_evidence({**_NUMPY_REQS, "train.py": train, "tests/test_x.py": test})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "outside the tests: tests/test_x.py. Unseeded: " in finding.message
    assert finding.remediation.startswith("Add a single set_all_seeds(seed) helper")


def test_the_seed_scaffold_earns_credit_only_once_an_entrypoint_calls_it(tmp_path):
    from typer.testing import CliRunner

    from adduce.cli import app
    from adduce.rules.base import Category

    def determinism() -> float:
        card = run_check(tmp_path).card
        return next(c.earned for c in card.categories if c.category is Category.DETERMINISM)

    (tmp_path / "requirements.txt").write_text("torch==2.1.0\nnumpy==1.26.0\n", encoding="utf-8")
    train = "import torch\n\n\ndef main():\n    print(torch.randn(3))\n\n\nif __name__ == '__main__':\n{call}    main()\n"
    (tmp_path / "train.py").write_text(train.format(call=""), encoding="utf-8")
    before = determinism()

    result = CliRunner().invoke(app, ["fix", str(tmp_path), "--scaffold", "seeds"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "seed_utils.py").is_file()
    assert determinism() == before

    called = "from seed_utils import set_all_seeds\n" + train.format(call="    set_all_seeds(0)\n")
    (tmp_path / "train.py").write_text(called, encoding="utf-8")
    assert determinism() > before


@pytest.mark.parametrize(
    "files, source",
    [
        pytest.param({"requirements.txt": "numpy==1.26.0\n"}, "import random\nrandom.seed(0)\n", id="random-seed"),
        pytest.param(_TORCH_REQS, "import numpy as np\nnp.random.seed(0)\n", id="numpy-seed"),
    ],
)
def test_one_direct_seed_alone_is_seeding_detected(make_evidence, files, source):
    # Control: green on base. Pins random.seed and numpy.random.seed as direct
    # seeds, without which the finding would say nothing is seeded.
    finding = SeedDeterminismRule().evaluate(make_evidence({**files, "model.py": source}))
    assert finding.status is Status.PARTIAL
    assert finding.message.startswith("Some RNG sources are seeded, but not all: missing ")


@pytest.mark.parametrize(
    "requirements, source",
    [
        pytest.param(
            "torch==2.1.0\nnumpy==1.26.0\n",
            "import random\nimport numpy as np\nimport torch\n"
            "random.seed(0)\nnp.random.seed(0)\ntorch.manual_seed(0)\ntorch.cuda.manual_seed(0)\n",
            id="torch-cuda-manual-seed",
        ),
        pytest.param(
            "tensorflow==2.15.0\n",
            "import random\nimport keras\nrandom.seed(0)\nkeras.utils.set_random_seed(0)\n",
            id="keras-set-random-seed",
        ),
        pytest.param(
            "jax==0.4.20\n", "import random\nimport jax\nrandom.seed(0)\nkey = jax.random.key(0)\n", id="jax-random-key"
        ),
    ],
)
def test_each_alternative_seed_call_seeds_its_source(make_evidence, requirements, source):
    # Control: green on base. Pins the alternative call for each source.
    ev = make_evidence({"requirements.txt": requirements, "model.py": source})
    assert SeedDeterminismRule().evaluate(ev).status is Status.PASS


def test_a_repository_with_no_seeding_call_says_none_was_detected(make_evidence):
    # Control: green on base. Pins the plain wording where nothing was found.
    ev = make_evidence({**_NUMPY_REQS, "model.py": "import numpy as np\nx = np.random.rand(3)\n"})
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.message.startswith("No seeding detected for the RNG libraries this repository uses: ")


def test_a_source_seeded_only_in_a_test_is_reported_in_one_statement(make_evidence):
    test = "import numpy as np\nimport pytest\n\nnp.random.seed(0)\n"
    ev = make_evidence(
        {
            "requirements.txt": "jax==0.4.20\nnumpy==1.26.0\n",
            "model.py": "import jax\nkey = jax.random.PRNGKey(0)\n",
            "tests/test_draws.py": test,
        }
    )
    finding = SeedDeterminismRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert finding.message == (
        "No seeding that counts was detected for the RNG libraries this repository uses: "
        "python (random.seed), numpy (np.random.seed or default_rng). Seeding for a missing source "
        "was found only in tests or in code adduce found no reference to outside the tests, which "
        "do not count: tests/test_draws.py."
    )
