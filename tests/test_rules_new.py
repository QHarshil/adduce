"""The research-artifact rules: drift, reconciliation, precision, checkpoints,
notebooks, portability, remotes, dependencies, archival."""

from __future__ import annotations

import json

import pytest

from adduce.rules.base import Status
from adduce.rules.checkpoint import OptimizerStateRule, RngStateRule
from adduce.rules.deps import GhostDependencyRule, NotebookOnlyImportRule
from adduce.rules.drift import HyperparameterDriftRule, MissingHyperparameterRule, values_match
from adduce.rules.notebook import ExecutionOrderRule, PipInstallCellRule
from adduce.rules.portability import AbsolutePathRule, SecretsRule
from adduce.rules.precision import AmpRule, TF32Rule
from adduce.rules.reconcile import MaterialDifferenceRule, SingleRunRule, UnbackedMetricRule
from adduce.rules.remote import HFRevisionRule
from adduce.rules.run import SlurmRequirementsRule

_TEX_LR = (
    "\\documentclass{article}\\begin{document}"
    "We use a learning rate of 1e-4 and report an accuracy of 92.4."
    "\\end{document}"
)


def test_values_match_rounding_awareness():
    assert values_match(0.814, 0.8137)
    assert values_match(1e-4, 0.0001)
    assert not values_match(0.814, 0.79)
    assert values_match(50, 50.0)


def test_a_trailing_zero_the_paper_printed_narrows_the_tolerance():
    """A float cannot remember a trailing zero, so the precision must be carried.

    ``f"{28.0:.10f}".rstrip("0")`` is ``"28."``, whose fractional part is empty,
    so a value the paper printed to a tenth was compared as though printed to a
    unit and 28.4 fell inside its tolerance. This is the shipped form of the
    defect: a drift rule reporting agreement between a paper and code that
    disagree.
    """
    assert values_match(28.0, 28.4)
    assert not values_match(28.0, 28.4, decimals=1)
    assert values_match(28.0, 28.03, decimals=1)
    # A paper that really did print `28` is agreed with by anything rounding to it.
    assert values_match(28.0, 28.4, decimals=0)


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        ("28.0", 1),
        ("28", 0),
        ("0.30", 2),
        ("-3.25", 2),
        ("  7.100 ", 3),
        # Scientific notation records no precision: counting its printed digits
        # would claim a tolerance of 0.5 on a number orders of magnitude smaller.
        ("1e-4", None),
        ("1.5e-3", None),
        ("10^{-3}", None),
    ],
)
def test_printed_precision_is_read_from_the_source_text(literal, expected):
    from adduce.evidence.latex import _printed_decimals

    assert _printed_decimals(literal) == expected


def test_a_statement_carries_the_precision_the_paper_printed(make_evidence):
    """End to end: the parse records it and the rule is what uses it."""
    ev = make_evidence(
        {
            "paper/main.tex": (
                "\\documentclass{article}\\begin{document}"
                "We train with a learning rate of 0.30 and a weight decay of 0.05."
                "\\end{document}"
            )
        }
    )
    stated = {v.name: v.decimals for v in ev.latex.hyperparameters}
    assert stated["learning_rate"] == 2
    assert stated["weight_decay"] == 2


def test_hyperparameter_drift_detected(make_evidence):
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,
            "configs/main.yaml": "lr: 0.001\n",  # paper says 1e-4
            "train.py": "import yaml\n",
        }
    )
    finding = HyperparameterDriftRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "learning_rate" in finding.message


def test_hyperparameter_agreement_passes(make_evidence):
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,
            "configs/main.yaml": "lr: 0.0001\n",
            "train.py": "import yaml\n",
        }
    )
    assert HyperparameterDriftRule().evaluate(ev).status is Status.PASS


def test_a_config_key_abbreviated_the_way_the_paper_writes_it_still_resolves(make_evidence):
    """``dec. depth`` is a key, and the space after the dot is not part of it.

    A dotted key resolves on its terminal segment, and the segment was taken
    unstripped -- so ``depth`` named a hyperparameter and `` depth`` named
    nothing, on a character that belongs to neither. MAE heads its own ablation
    column ``dec. depth``, and a config recording that ablation writes the key
    the same way; unresolved, the rule reports the paper's decoder depth as
    having no counterpart in code, which the config states outright.
    """
    ev = make_evidence(
        {
            "paper/main.tex": (
                "\\documentclass{article}\\begin{document}"
                "We use a decoder depth of 8 blocks."
                "\\end{document}"
            ),
            "configs/ablation.yaml": '"dec. depth": 8\n',
            "train.py": "import yaml\n",
        }
    )
    assert MissingHyperparameterRule().evaluate(ev).status is Status.PASS
    assert HyperparameterDriftRule().evaluate(ev).status is Status.PASS


def test_materialized_config_outranks_static(make_evidence):
    # Paper says 1e-4; static config disagrees, but the Hydra output (what
    # actually ran) agrees — the authoritative source wins, no drift.
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,
            "configs/main.yaml": "lr: 0.001\n",
            "outputs/run1/.hydra/config.yaml": "lr: 0.0001\n",
            ".adduce/manifest.yaml": (
                "schema: adduce/1\n"
                "claims:\n"
                "  - id: C1\n"
                "    status: confirmed\n"
                "    produced_by:\n"
                "      config: outputs/run1/.hydra/config.yaml\n"
            ),
            "train.py": "import yaml\n",
        }
    )
    assert HyperparameterDriftRule().evaluate(ev).status is Status.PASS


def test_unlinked_materialized_config_does_not_override_committed_config(make_evidence):
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,
            "configs/main.yaml": "lr: 0.001\n",
            "outputs/unrelated/.hydra/config.yaml": "lr: 0.0001\n",
            "train.py": "import yaml\n",
        }
    )

    finding = HyperparameterDriftRule().evaluate(ev)

    assert finding.status is Status.FAIL
    assert "configs/main.yaml" in finding.message


def test_missing_hyperparameter_rule(make_evidence):
    ev = make_evidence({"paper/main.tex": _TEX_LR, "train.py": "print('no configs at all')\n"})
    finding = MissingHyperparameterRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "learning_rate" in finding.message


def test_reconcile_material_difference(make_evidence):
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,  # accuracy 92.4
            "results/eval.csv": "epoch,accuracy\n1,85.0\n",
            "train.py": "pass\n",
        }
    )
    finding = MaterialDifferenceRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "92.4" in finding.message


def test_reconcile_rounding_passes(make_evidence):
    ev = make_evidence(
        {
            "paper/main.tex": _TEX_LR,
            "results/eval.csv": "epoch,accuracy\n1,92.41\n",
            "train.py": "pass\n",
        }
    )
    assert MaterialDifferenceRule().evaluate(ev).status is Status.PASS


def test_reconcile_uses_manifest_declared_log_not_closest_unrelated_run(make_evidence):
    ev = make_evidence(
        {
            ".adduce/manifest.yaml": (
                "schema: adduce/1\n"
                "claims:\n"
                "  - id: C1\n"
                "    status: confirmed\n"
                "    metric: accuracy\n"
                "    value: 92.4\n"
                "    produced_by:\n"
                "      log: results/main.csv\n"
            ),
            "results/main.csv": "epoch,accuracy\n1,85.0\n",
            "results/unrelated.csv": "epoch,accuracy\n1,92.4\n",
        }
    )

    finding = MaterialDifferenceRule().evaluate(ev)

    assert finding.status is Status.PARTIAL
    assert "closest logged 85" in finding.message


def test_reconcile_does_not_treat_statusless_log_as_authority(make_evidence):
    ev = make_evidence(
        {
            ".adduce/manifest.yaml": (
                "schema: adduce/1\n"
                "claims:\n"
                "  - id: C1\n"
                "    metric: accuracy\n"
                "    value: 92.4\n"
                "    produced_by:\n"
                "      log: results/main.csv\n"
            ),
            "results/main.csv": "epoch,accuracy\n1,85.0\n",
            "results/unrelated.csv": "epoch,accuracy\n1,92.4\n",
        }
    )

    finding = MaterialDifferenceRule().evaluate(ev)

    assert finding.status is Status.PASS


def test_unbacked_metric_na_without_results(make_evidence):
    ev = make_evidence({"paper/main.tex": _TEX_LR, "train.py": "pass\n"})
    assert UnbackedMetricRule().evaluate(ev).status is Status.NOT_APPLICABLE


def test_single_run_rule_seed_sweep_passes(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "train.py": "import torch\n",
            "scripts/sweep.sh": "python train.py --seed 0\npython train.py --seed 1\npython train.py --seed 2\n",
        }
    )
    assert SingleRunRule().evaluate(ev).status is Status.PASS


def test_tf32_undocumented_is_partial_never_fail(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "train.py": "import torch\ntorch.backends.cuda.matmul.allow_tf32 = True\n",
        }
    )
    finding = TF32Rule().evaluate(ev)
    assert finding.status is Status.PARTIAL


def test_amp_documented_passes(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "README.md": "# X\n\n## Hardware and precision\n\nbf16 autocast on A100.\n",
            "train.py": "import torch\nwith torch.autocast('cuda'):\n    pass\n",
        }
    )
    assert AmpRule().evaluate(ev).status is Status.PASS


def test_checkpoint_weights_only_flagged(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\n",
            "train.py": "import torch\ntorch.save(model.state_dict(), 'model.pt')\n",
        }
    )
    assert OptimizerStateRule().evaluate(ev).status is Status.PARTIAL


def test_checkpoint_complete_dict_passes(make_evidence):
    source = (
        "import torch\n"
        "ckpt = {'model': m.state_dict(), 'optimizer': o.state_dict(), 'epoch': e,\n"
        "        'rng_state': torch.get_rng_state(), 'config': cfg}\n"
        "torch.save(ckpt, 'ckpt.pt')\n"
    )
    ev = make_evidence({"requirements.txt": "torch==2.1.0\n", "train.py": source})
    assert OptimizerStateRule().evaluate(ev).status is Status.PASS
    assert RngStateRule().evaluate(ev).status is Status.PASS


def test_framework_import_does_not_prove_checkpoint_completeness(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "torch==2.1.0\nlightning==2.4.0\n",
            "train.py": (
                "import torch\n"
                "import lightning\n"
                "torch.save(model.state_dict(), 'model.pt')\n"
            ),
        }
    )

    assert OptimizerStateRule().evaluate(ev).status is Status.PARTIAL
    assert RngStateRule().evaluate(ev).status is Status.PARTIAL


def test_notebook_rules(make_evidence):
    notebook = json.dumps(
        {
            "cells": [
                {"cell_type": "code", "source": ["!pip install torch"], "execution_count": 7, "outputs": []},
                {"cell_type": "code", "source": ["print(1)"], "execution_count": 2, "outputs": []},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    ev = make_evidence({"nb.ipynb": notebook})
    assert ExecutionOrderRule().evaluate(ev).status is Status.PARTIAL
    assert PipInstallCellRule().evaluate(ev).status is Status.PARTIAL


def test_portability_rules(make_evidence):
    ev = make_evidence(
        {"load.py": "p = '/Users/alice/x.csv'\nk = 'ghp_" + "a" * 36 + "'\n"}
    )
    assert AbsolutePathRule().evaluate(ev).status in (Status.PARTIAL, Status.FAIL)
    secrets = SecretsRule().evaluate(ev)
    assert secrets.status is Status.FAIL
    assert "ghp_" not in secrets.message


def test_hf_revision_rule(make_evidence):
    ev = make_evidence(
        {
            "model.py": "from transformers import AutoModel\nAutoModel.from_pretrained('bert-base-uncased')\n"
        }
    )
    finding = HFRevisionRule().evaluate(ev)
    assert finding.status is Status.FAIL
    assert "forward" not in finding.message  # the caveat lives in the remediation
    assert "verify" in finding.remediation


def test_ghost_dependency_detected(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "numpy==1.26.0\n",
            "train.py": "import numpy\nimport cv2\nimport yaml\n",
        }
    )
    finding = GhostDependencyRule().evaluate(ev)
    assert finding.status in (Status.PARTIAL, Status.FAIL)
    assert "opencv-python" in finding.message and "pyyaml" in finding.message


def test_ghost_dependency_respects_naming_map(make_evidence):
    ev = make_evidence(
        {
            "requirements.txt": "scikit-learn==1.4.0\nopencv-python==4.9.0\npyyaml==6.0\n",
            "train.py": "import sklearn\nimport cv2\nimport yaml\n",
        }
    )
    assert GhostDependencyRule().evaluate(ev).status is Status.PASS


def test_notebook_only_import_rule(make_evidence):
    notebook = json.dumps(
        {
            "cells": [{"cell_type": "code", "source": ["import seaborn\n"], "execution_count": 1, "outputs": []}],
            "metadata": {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    ev = make_evidence(
        {"requirements.txt": "numpy==1.26.0\n", "nb.ipynb": notebook, "train.py": "import numpy\n"}
    )
    finding = NotebookOnlyImportRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "seaborn" in finding.message


def test_slurm_requirements_rule(make_evidence):
    ev = make_evidence(
        {
            "train.py": "pass\n",
            "jobs/train.slurm": "#!/bin/bash\n#SBATCH --gres=gpu:4\npython train.py\n",
        }
    )
    finding = SlurmRequirementsRule().evaluate(ev)
    assert finding.status is Status.PARTIAL
    assert "gpu:4" in finding.message
