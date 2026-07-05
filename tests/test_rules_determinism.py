"""The determinism rules: layered grading, framework gating, false-positive control."""

from __future__ import annotations

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
    assert "CUBLAS_WORKSPACE_CONFIG" in finding.message


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
    assert finding.status is Status.FAIL
    assert finding.locations[0].path == "data.py"


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
