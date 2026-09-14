"""The research-artifact collectors: config, LaTeX, notebooks, remotes,
precision, results, run history, portability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adduce.evidence.portability import secret_kind
from adduce.rules.base import Status
from adduce.rules.remote import RawUrlRule

_TEX = r"""
\documentclass{article}
\title{CineMatch: Personalized Movie Recommendation}
\begin{document}
% a comment with a learning rate of 999 that must be ignored
We train with a learning rate of $1\times10^{-4}$ and a batch size of 256
for 50 epochs on CIFAR-10, using three seeds and reporting mean $\pm$ std.
Our model achieves an accuracy of 92.4 on the test set.
All experiments ran on a single NVIDIA A100 GPU for 3 hours in bf16.
We include an ablation over attention heads.
\begin{tabular}{lcc}
\toprule
Model & Accuracy & F1 \\
\midrule
Ours & 92.4 & 89.1 \\
Baseline & 90.2 & 87.0 \\
\bottomrule
\end{tabular}
\end{document}
"""


def test_latex_extraction(make_evidence):
    ev = make_evidence({"paper/main.tex": _TEX})
    latex = ev.latex
    assert latex.has_paper and latex.main_file == "paper/main.tex"
    assert latex.title == "CineMatch: Personalized Movie Recommendation"

    hp = latex.hyperparameter_values()
    assert any(abs(v.value - 1e-4) < 1e-12 for v in hp.get("learning_rate", []))
    assert any(v.value == 256 for v in hp.get("batch_size", []))
    assert any(v.value == 50 for v in hp.get("epochs", []))

    assert any(m.name == "accuracy" and abs(m.value - 92.4) < 1e-9 for m in latex.metrics)
    assert any(c.row_label == "Ours" and c.value == 92.4 for c in latex.table_cells)
    assert "cifar-10" in latex.datasets_mentioned
    assert latex.mentions_hardware and latex.mentions_runtime
    assert latex.mentions_multiseed and latex.mentions_precision
    assert latex.ablation_mentions
    # Comment-stripped: the bogus 999 never appears.
    assert not any(v.value == 999 for values in hp.values() for v in values)


def test_config_collector_flattens_and_normalises(make_evidence):
    ev = make_evidence(
        {
            "configs/main.yaml": "optimizer:\n  lr: 0.0001\n  weight_decay: 0.01\ntrain:\n  batch_size: 256\n",
            "train.py": "import yaml\n",
        }
    )
    config = ev.config
    assert len(config.files) == 1
    assert config.files[0].values["optimizer.lr"] == 0.0001
    hp = config.hyperparameters()
    assert any(v == 0.0001 for v, _, _ in hp["learning_rate"])
    assert any(v == 256 for v, _, _ in hp["batch_size"])


def test_hydra_and_deepspeed_detection(make_evidence):
    ev = make_evidence(
        {
            "conf/config.yaml": "defaults:\n  - model: resnet\nlr: 0.001\n",
            "configs/ds_config.json": json.dumps({"zero_optimization": {"stage": 2}, "fp16": {"enabled": True}}),
            "train.py": "import hydra\n",
        }
    )
    assert ev.config.uses_hydra
    assert any(f.is_deepspeed for f in ev.config.files)


def _notebook(cells: list[dict], metadata: dict | None = None) -> str:
    return json.dumps(
        {"cells": cells, "metadata": metadata if metadata is not None else {"kernelspec": {"name": "python3"}, "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
    )


def _code_cell(source: str, count: int | None = None, outputs: bool = False) -> dict:
    return {
        "cell_type": "code",
        "source": [source],
        "execution_count": count,
        "outputs": [{"output_type": "stream", "text": "x"}] if outputs else [],
    }


def test_notebook_collector(make_evidence):
    disordered = _notebook(
        [
            _code_cell("import torch\n!pip install torch", count=5, outputs=True),
            _code_cell("df = pd.read_csv('/Users/alice/data.csv')", count=2),
            _code_cell("torch.rand(3)", count=9),
        ]
    )
    ev = make_evidence({"analysis.ipynb": disordered})
    nb = ev.notebooks.notebooks[0]
    assert not nb.monotonic and nb.has_gaps and nb.has_outputs
    assert nb.pip_install_cells and nb.abs_path_cells
    assert "torch" in nb.imports
    assert nb.uses_randomness and nb.seed_before_randomness is False


def test_notebook_companion_script_detected(make_evidence):
    clean = _notebook([_code_cell("print(1)", count=1, outputs=True)])
    ev = make_evidence({"analysis.ipynb": clean, "analysis.py": "print(1)\n"})
    assert ev.notebooks.notebooks[0].has_companion_script


def test_remote_collector_pins(make_evidence):
    sha = "8" * 40
    source = (
        "from transformers import AutoModel\n"
        "from datasets import load_dataset\n"
        "import torch\n"
        f"pinned = AutoModel.from_pretrained('bert-base-uncased', revision='{sha}')\n"
        "floating = AutoModel.from_pretrained('bert-base-uncased')\n"
        "tagged = AutoModel.from_pretrained('gpt2', revision='v1.0')\n"
        "ds = load_dataset('squad')\n"
        "hub = torch.hub.load('pytorch/vision:v0.10.0', 'resnet18')\n"
    )
    ev = make_evidence({"model.py": source, "get.sh": "wget https://example.org/weights.bin\n"})
    refs = ev.remote.references
    ordering = [(r.file, r.line, r.kind, r.spec, r.pin_detail) for r in refs]
    assert ordering == sorted(ordering)
    hf = [r for r in refs if r.kind == "hf"]
    assert sum(1 for r in hf if r.pinned) == 1
    assert any(r.pin_detail == "mutable-ref" for r in hf)
    assert any(r.kind == "torch_hub" and not r.pinned for r in refs)
    assert any(r.kind == "url" for r in refs)


def test_unrelated_checksum_file_does_not_cover_raw_download(make_evidence):
    ev = make_evidence(
        {
            "get.sh": "curl https://example.org/model.bin -o model.bin\n",
            "checksums.txt": "0" * 64 + "  different.bin\n",
        }
    )

    reference = next(r for r in ev.remote.references if r.kind == "url")

    assert reference.pin_detail == "none"
    assert RawUrlRule().evaluate(ev).status is Status.FAIL


def test_named_checksum_command_covers_its_raw_download(make_evidence):
    ev = make_evidence(
        {
            "get.sh": (
                "curl https://example.org/model.bin -o model.bin\n"
                "echo '"
                + "0" * 64
                + "  model.bin' | sha256sum -c -\n"
            )
        }
    )

    reference = next(r for r in ev.remote.references if r.kind == "url")

    assert reference.pin_detail == "checksum"
    assert RawUrlRule().evaluate(ev).status is Status.PASS


def test_checksum_probe_is_only_consulted_for_lines_carrying_a_reference(
    make_evidence, monkeypatch
):
    """The probe answers a question only the URL and bucket branches ask.

    It searches its own line for a download target and reads up to three more,
    so consulting it once per line made every line of every shell script and
    module pay for a question almost none of them raise.
    """
    from adduce.evidence import remote as remote_module

    original = remote_module._download_has_bound_checksum
    consulted: list[int] = []

    def counted(lines: list[str], index: int) -> bool:
        consulted.append(index)
        return original(lines, index)

    monkeypatch.setattr(remote_module, "_download_has_bound_checksum", counted)

    filler = "".join(f"value_{n}={n}\n" for n in range(200))
    ev = make_evidence(
        {"get.sh": filler + "curl https://example.org/model.bin -o model.bin\n"}
    )

    assert consulted == [200]
    assert [r.pin_detail for r in ev.remote.references if r.kind == "url"] == ["none"]


def test_precision_collector(make_evidence):
    source = (
        "import torch\n"
        "torch.backends.cuda.matmul.allow_tf32 = True\n"
        "torch.set_float32_matmul_precision('high')\n"
        "with torch.autocast('cuda', dtype=torch.bfloat16):\n"
        "    pass\n"
        "scaler = torch.cuda.amp.GradScaler()\n"
        "model = model.half()\n"
    )
    ev = make_evidence({"train.py": source})
    assert ev.precision.uses_tf32
    assert ev.precision.uses_amp
    assert ev.precision.uses_low_precision


def test_results_collector(make_evidence):
    ev = make_evidence(
        {
            "results/eval.csv": "epoch,accuracy,loss\n1,0.90,0.5\n2,0.921,0.4\n",
            "results/final.json": json.dumps({"ndcg@10": 0.8137, "runtime": 120}),
            "logs/events.out.tfevents.123.host": "binary",
        }
    )
    results = ev.results
    assert results.present and results.has_tensorboard
    accuracy = results.lookup_metric("accuracy")
    assert accuracy and 0.921 in accuracy[0][1]
    assert results.lookup_metric("ndcg@10")


def test_result_lookup_normalises_manifest_path_spelling(make_evidence):
    results = make_evidence(
        {"results/eval.csv": "epoch,accuracy\n1,0.9\n"}
    ).results

    assert results.lookup_metric("accuracy", path="./results/eval.csv")
    assert results.lookup_metric("accuracy", path=r"results\eval.csv")


def test_run_history_collector(make_evidence):
    ev = make_evidence(
        {
            "scripts/run_all.sh": (
                "#!/bin/bash\n"
                "#SBATCH --gres=gpu:2\n"
                "#SBATCH --time=12:00:00\n"
                "python train.py --config configs/main.yaml --seed 42 model.lr=0.0001\n"
            ),
            "outputs/2024-01-01/.hydra/config.yaml": "lr: 0.0002\nbatch_size: 128\n",
        }
    )
    runs = ev.runs
    assert runs.commands and runs.commands[0].seeds == (42,)
    assert runs.commands[0].config_path == "configs/main.yaml"
    assert ("model.lr", "0.0001") in runs.commands[0].overrides
    assert runs.slurm_scripts and runs.slurm_scripts[0].gpu_request
    assert runs.materialized and runs.materialized[0].source == "hydra"
    assert runs.materialized[0].values["lr"] == 0.0002


def test_portability_collector(make_evidence):
    ev = make_evidence(
        {
            "load.py": "path = '/Users/alice/data/train.csv'\nurl = 'http://localhost:8080/api'\n",
            "config.yaml": "key: AKIAIOSFODNN7EXAMPLE\n",
        }
    )
    kinds = {h.kind for h in ev.portability.hits}
    assert kinds == {"abs_path", "localhost", "secret"}
    secret = ev.portability.of_kind("secret")[0]
    assert "AKIA" not in secret.detail  # never echo the value


def test_the_secret_detector_names_a_kind_without_echoing_the_value():
    """The single detector every other layer reuses; a second table would drift."""
    assert secret_kind("AKIA" + "IOSFODNN7EXAMPLE") == "AWS access key"
    assert secret_kind(["ordinary", "hf_" + "c" * 30]) == "Hugging Face token"
    assert secret_kind("learning rate") is None
    assert secret_kind(0.001) is None
    assert secret_kind(None) is None


def test_secret_scanning_includes_documentation_and_manifest(make_evidence):
    ev = make_evidence(
        {
            "README.md": "Example accidentally contains ghp_" + "a" * 36 + "\n",
            ".adduce/manifest.yaml": "schema: adduce/1\ntoken: hf_" + "b" * 30 + "\n",
        }
    )

    hits = ev.portability.of_kind("secret")

    assert {hit.file for hit in hits} == {"README.md", ".adduce/manifest.yaml"}
    assert all("ghp_" not in hit.detail and "hf_" not in hit.detail for hit in hits)


def test_plural_keyword_is_a_count_not_a_value(make_evidence):
    tex = (
        "\\documentclass{article}\\begin{document}"
        "Results are averaged over 3 seeds with seed 42 as the base."
        "\\end{document}"
    )
    ev = make_evidence({"paper/main.tex": tex})
    seeds = ev.latex.hyperparameter_values().get("seed", [])
    # "3 seeds" is a count and must not be extracted; "seed 42" is a value.
    assert all(v.value != 3 for v in seeds)
    assert any(v.value == 42 for v in seeds)


@pytest.mark.parametrize(
    ("prose", "expected"),
    [
        # A learning rate scaled by the batch: the number after the keyword is
        # the fraction's denominator, and the paper states its batch size
        # elsewhere.
        (r"multiply the learning rate by $\frac{\mbox{batch size}}{256}$", None),
        (r"$\frac{\mathrm{lr}}{\mathrm{batchsize}}{512}$", None),
        # A revision macro, where the keyword ends the old text and the number
        # opens the new. A newline between the braces is still one command's
        # two arguments.
        (r"\oldnew{two 3-layers}{a 3-layer} perceptron", None),
        # A closing brace alone is not a boundary: these are real statements.
        (r"\textbf{batch size:} 512", 512.0),
        (r"\textbf{Batch size}: 16", 16.0),
        # And neither is a brace closing with anything at all between it and
        # the next opening. This is why the rule is adjacency and not "a group
        # closed somewhere": a table header closing and an italic cell opening
        # is not one command's two arguments, and 28.6 is a real BLEU.
        (r"BLEU} & {\it 28.6}", 28.6),
        # A boundary *after* the number is not a boundary before it. This is
        # the shape that separates examining the gap from examining the whole
        # window: a paper states its batch size in one clause and the scaling
        # fraction in the next, so a window-wide search would refuse the real
        # value because of a fraction it has already passed.
        (r"\textbf{Batch size}: 16, then scale by $\frac{lr}{512}$", 16.0),
    ],
)
def test_a_number_in_a_sibling_group_is_not_the_keywords_value(prose, expected, make_evidence):
    r"""A keyword ending one argument and a number opening the next is not a statement.

    The distinction is a group closing *and another opening*, not a brace.
    Reading the divisor of a scaling rule reported a batch size the paper does
    not state, which is a wrong number rather than a missing one, and it
    reaches an R-DRIFT-001 verdict.
    """
    names = {"batch_size", "num_layers", "bleu"}
    latex = make_evidence({"paper/main.tex": prose}).latex
    read = [v.value for v in (*latex.hyperparameters, *latex.metrics) if v.name in names]
    assert read == ([] if expected is None else [expected])


def test_the_group_boundary_fixture_agrees_with_its_own_config():
    """The synthetic case reads one batch size, and it is the one both sides state."""
    from adduce.evidence.latex import _HYPERPARAM_PATTERNS, _extract_keyword_values

    case = Path(__file__).resolve().parent.parent / "corpus" / "synthetic"
    text = (case / "synthetic_group_boundary_value" / "paper" / "main.tex").read_text(
        encoding="utf-8"
    )
    values = _extract_keyword_values(text, "main.tex", _HYPERPARAM_PATTERNS, "hyperparameter")
    assert [v.value for v in values if v.name == "batch_size"] == [4096.0]


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        # The trailing zero a float cannot remember, which is the whole point.
        ("0.30", 2),
        ("0.3", 1),
        ("28", 0),
        ("28.000", 3),
        # Scientific notation prints no fractional digits and yet states a
        # precision its decimal expansion is what expresses, so counting its
        # printed digits would claim a tolerance orders of magnitude too wide.
        ("1e-4", None),
        (r"10^{-3}", None),
        ("", None),
    ],
)
def test_printed_decimals_counts_what_the_paper_printed(literal, expected):
    from adduce.evidence.latex import _printed_decimals

    assert _printed_decimals(literal) == expected


def test_a_paper_value_records_the_precision_it_was_printed_at(make_evidence):
    """``decimals`` comes from the source text, because the float cannot carry it."""
    ev = make_evidence({"paper/main.tex": "We use a learning rate of 0.30 in every run.\n"})
    rates = ev.latex.hyperparameter_values()["learning_rate"]
    assert [(v.value, v.decimals) for v in rates] == [(0.3, 2)]
