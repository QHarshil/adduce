"""Name resolution, wrapper expansion, and structural extraction from Python sources."""

from __future__ import annotations


def test_direct_call_resolution(make_evidence):
    ev = make_evidence({"train.py": "import torch\ntorch.manual_seed(0)\n"})
    assert ev.py.calls("torch.manual_seed")
    sites = ev.py.call_sites("torch.manual_seed")
    assert sites[0].file == "train.py" and sites[0].line == 2


def test_import_alias_resolution(make_evidence):
    ev = make_evidence({"train.py": "import torch as th\nth.manual_seed(0)\nth.cuda.manual_seed_all(0)\n"})
    assert ev.py.calls("torch.manual_seed")
    assert ev.py.calls("torch.cuda.manual_seed_all")


def test_from_import_resolution(make_evidence):
    ev = make_evidence({"train.py": "from torch import manual_seed as ms\nms(0)\n"})
    assert ev.py.calls("torch.manual_seed")


def test_from_import_submodule(make_evidence):
    ev = make_evidence(
        {"train.py": "from torch.backends import cudnn\ncudnn.benchmark = False\n"}
    )
    assert ev.py.assigns("torch.backends.cudnn.benchmark", False)


def test_numpy_conventional_alias(make_evidence):
    ev = make_evidence({"train.py": "import numpy as np\nnp.random.seed(42)\n"})
    assert ev.py.calls("numpy.random.seed")


def test_one_hop_wrapper_same_file(make_evidence):
    source = (
        "import random\nimport numpy as np\nimport torch\n\n"
        "def set_seed(seed):\n"
        "    random.seed(seed)\n"
        "    np.random.seed(seed)\n"
        "    torch.manual_seed(seed)\n\n"
        "set_seed(0)\n"
    )
    ev = make_evidence({"train.py": source})
    assert ev.py.calls("torch.manual_seed")
    assert ev.py.calls("numpy.random.seed")
    assert ev.py.calls("random.seed")


def test_one_hop_wrapper_cross_module(make_evidence):
    ev = make_evidence(
        {
            "utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n",
            "train.py": "from utils import seed_everything\nseed_everything(0)\n",
        }
    )
    assert ev.py.calls("torch.manual_seed")


def test_one_hop_wrapper_via_module_attribute(make_evidence):
    ev = make_evidence(
        {
            "utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n",
            "train.py": "import utils\nutils.seed_everything(0)\n",
        }
    )
    assert ev.py.calls("torch.manual_seed")


def test_call_inside_uninvoked_helper_still_counts(make_evidence):
    ev = make_evidence(
        {"utils.py": "import torch\n\ndef seed_everything(seed):\n    torch.manual_seed(seed)\n"}
    )
    # Deliberate: a primitive appearing anywhere counts, even inside a helper
    # never invoked in the scanned tree (it may be called from a notebook or
    # shell). Erring this way avoids false "you did not seed" findings, at the
    # cost of trusting dead code; full reachability analysis is out of scope.
    assert ev.py.calls("torch.manual_seed")


def test_env_assignment_and_setdefault(make_evidence):
    source = (
        "import os\n"
        "os.environ['PYTHONHASHSEED'] = '0'\n"
        "os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')\n"
    )
    ev = make_evidence({"a.py": source})
    assert ev.py.sets_env("PYTHONHASHSEED")
    assert ev.py.sets_env("CUBLAS_WORKSPACE_CONFIG")


def test_dataloader_extraction(make_evidence):
    source = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "g = torch.Generator()\n"
        "good = DataLoader(ds, shuffle=True, generator=g, num_workers=4, worker_init_fn=fn)\n"
        "bad = DataLoader(ds, shuffle=True, num_workers=2)\n"
        "cpu_only = DataLoader(ds, shuffle=False)\n"
    )
    ev = make_evidence({"data.py": source})
    assert len(ev.py.dataloaders) == 3
    gaps = ev.py.dataloader_gaps()
    assert len(gaps) == 1
    assert gaps[0].line == 5


def test_dataloader_with_sampler_is_not_a_shuffle_gap(make_evidence):
    source = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(ds, sampler=sampler)\n"
    )
    ev = make_evidence({"data.py": source})
    assert ev.py.dataloader_gaps() == []


def test_non_torch_dataloader_ignored(make_evidence):
    ev = make_evidence({"a.py": "from mylib import DataLoader\nDataLoader(x, shuffle=True)\n"})
    assert ev.py.dataloaders == []


def test_sklearn_random_state_detection(make_evidence):
    source = (
        "from sklearn.ensemble import RandomForestClassifier\n"
        "from sklearn.model_selection import train_test_split\n"
        "clf = RandomForestClassifier(n_estimators=100, random_state=0)\n"
        "train_test_split(X, y)\n"
    )
    ev = make_evidence({"model.py": source})
    assert len(ev.py.estimators) == 2
    unseeded = ev.py.unseeded_estimators()
    assert len(unseeded) == 1
    assert unseeded[0].qualname.endswith("train_test_split")


def test_inline_suppression_parsing(make_evidence):
    ev = make_evidence(
        {"a.py": "import torch\nx = 1  # adduce: ignore=R-DET-001, R-DET-002\n"}
    )
    assert ev.py.suppressions["a.py"][2] == {"R-DET-001", "R-DET-002"}


def test_syntax_error_does_not_crash(make_evidence):
    ev = make_evidence({"broken.py": "def f(:\n", "ok.py": "import torch\ntorch.manual_seed(0)\n"})
    assert any(m.parse_error for m in ev.py.modules)
    assert ev.py.calls("torch.manual_seed")


def test_main_guard_detection(make_evidence):
    ev = make_evidence({"cli.py": "if __name__ == '__main__':\n    pass\n"})
    assert ev.py.main_guard_files == ["cli.py"]


def test_numpy_generator_counts_as_seeding(make_evidence):
    ev = make_evidence({"a.py": "import numpy as np\nrng = np.random.default_rng(42)\n"})
    assert ev.py.uses_numpy_generator
