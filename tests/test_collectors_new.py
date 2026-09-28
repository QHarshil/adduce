"""The research-artifact collectors: config, LaTeX, notebooks, remotes,
precision, results, run history, portability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adduce.evidence.latex import _STATE_COMMANDS, _strip_state_commands
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


def test_a_cutoff_glued_to_a_metric_name_is_not_a_value(make_evidence):
    r"""``Recall@1`` is the metric's name, and the 1 is the rank, not the recall.

    The guard rejecting a number glued to a word did not hold for ``@``, so
    these were read as a recall of 1, a BLEU of 4 and an MRR of 1. The cutoff
    sits either side of the keyword boundary -- the pattern ``\brecall\b``
    leaves the ``@`` ahead of the number and the pattern ``recall@`` takes it
    into the match -- so both sides are refused, and the sentence's own +2.7 is
    not a result either.
    """
    tex = (
        "Our model improves image-text retrieval by +2.7\\% in average recall@1,\n"
        "and captioning by +2.8\\% in CIDEr.\n"
        "Method & MRR$\\uparrow$ & R@1$\\uparrow$ & R@5$\\uparrow$ \\\\\n"
        "C: CIDEr, S: SPICE, B@4: BLEU@4.\n"
    )
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert [(m.name, m.value) for m in metrics] == []


def test_the_number_after_a_cutoff_is_the_one_the_sentence_states(make_evidence):
    r"""A cutoff is passed over, not treated as the end of the search.

    "Recall@1 of 82.5" is how a retrieval paper states a result, and refusing
    the candidate outright rather than skipping the rank would lose the 82.5,
    turning a false positive into a miss on the commonest shape in the class.

    Both of the vocabulary's patterns for this metric match the one phrase, so
    the collector reads it twice and clustering merges them; the assertion is
    over the pair read, not over how many times one pattern list matched it.
    """
    tex = "We reach a recall@1 of 82.5 on the COCO test split.\n"
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert {(m.name, m.value) for m in metrics} == {("recall", 82.5)}


def test_a_number_glued_to_a_name_by_a_hyphen_is_still_refused(make_evidence):
    """The characters the guard already rejected keep being rejected.

    ``CIFAR-10`` is a dataset and ``top-1`` a column: neither states a value,
    and the ``@`` case is added beside them rather than in place of them.
    """
    tex = "We report accuracy on CIFAR-10 and follow the top-1 protocol.\n"
    metrics = make_evidence({"paper/main.tex": tex}).latex.metrics
    assert [(m.name, m.value) for m in metrics] == []

def test_a_counter_is_not_a_measurement(make_evidence):
    r"""A counter or length named for a hyperparameter states no hyperparameter.

    ``\newcounter{layers}`` and ``\newlength{\headsep}`` print nothing, but each
    puts a hyperparameter keyword in front of whatever number the prose states
    next, and the keyword scan read that number as its value: here a layer count
    of 4 and a head count of 2. This is a *use* of a command rather than a
    definition of one.
    """
    tex = (
        "\\documentclass{article}\n"
        "\\newcounter{layers}\n"
        "\\begin{document}\n"
        "\\stepcounter{layers} Table 4 restates the main results.\n"
        "\\newlength{\\headsep} Appendix 2 lists every run.\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert latex.hyperparameters == []
    assert [(m.name, m.value) for m in latex.metrics] == [("accuracy", 91.4)]


#: One call per command the guard covers, each named for a hyperparameter and
#: followed by a number close enough to be read. On this line the one-argument
#: commands (``stepcounter``, ``refstepcounter``, ``newcounter``, ``newlength``)
#: yield a phantom hyperparameter without the guard. The two-argument ones are
#: also refused by ``_crosses_group_boundary``, because their number sits in the
#: next brace group; the guard removes them anyway, so neither guard alone holds
#: that line.
_STATE_COMMAND_PROBES = {
    "setcounter": r"\setcounter{layers}{2}",
    "addtocounter": r"\addtocounter{layers}{1}",
    "stepcounter": r"\stepcounter{layers}",
    "refstepcounter": r"\refstepcounter{layers}",
    "setlength": r"\setlength{\layersep}{4pt}",
    "addtolength": r"\addtolength{\layersep}{2pt}",
    "newcounter": r"\newcounter{layers}",
    "newlength": r"\newlength{\headsep}",
}


def test_every_covered_state_command_has_a_probe():
    """A command added to the guard without a probe would be an unexercised guard."""
    assert set(_STATE_COMMAND_PROBES) == set(_STATE_COMMANDS)


@pytest.mark.parametrize("command", sorted(_STATE_COMMAND_PROBES))
def test_a_typesetting_assignment_states_no_hyperparameter(command, make_evidence):
    """Each covered command, on its own, against a number close enough to be read."""
    tex = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        + _STATE_COMMAND_PROBES[command]
        + " We report 4 configurations.\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert latex.hyperparameters == []
    assert [(m.name, m.value) for m in latex.metrics] == [("accuracy", 91.4)]


def test_removing_a_counter_assignment_does_not_move_the_line_beneath_it(make_evidence):
    """A locator is what sends a reader to a number, so the line count is kept.

    The first assignment's argument spans two lines, so removing it without
    keeping its line break would move every locator beneath it up by one.
    """
    tex = (
        "\\documentclass{article}\n"
        "\\setlength{\\tabcolsep}{\n"
        "  6pt}\n"
        "\\setcounter{tocdepth}{2}\n"
        "\\begin{document}\n"
        "Our model reaches an accuracy of 91.4 on the held-out split.\n"
        "\\end{document}\n"
    )
    latex = make_evidence({"paper/main.tex": tex}).latex
    assert [(m.value, m.line) for m in latex.metrics] == [(91.4, 6)]


def test_a_malformed_counter_assignment_is_left_alone():
    r"""Every argument the command declares must be there for the call to go.

    ``\setcounter`` takes two groups. One written with one is malformed, and
    removing the name alone would leave its arguments standing as text, which is
    the failure the removal exists to prevent rather than a lesser version of it.
    """
    text = "\\setcounter{tocdepth 2} and the depth is 4 layers\n"
    assert _strip_state_commands(text) == text


def test_a_counter_assignment_a_paper_prints_is_left_in_place():
    """Inside a verbatim block the assignment is what the page displays."""
    text = (
        "\\setcounter{tocdepth}{2}\n"
        "\\begin{verbatim}\n"
        "\\setcounter{tocdepth}{2}\n"
        "\\end{verbatim}\n"
    )
    assert _strip_state_commands(text) == (
        "\n\\begin{verbatim}\n\\setcounter{tocdepth}{2}\n\\end{verbatim}\n"
    )


def _table(column: str, value: str) -> str:
    """A minimal two-row ``tabular``: one header, one numeric cell."""
    return (
        "\\begin{tabular}{lc}\n"
        "Model & " + column + " \\\\\n"
        "Ours & " + value + " \\\\\n"
        "\\end{tabular}\n"
    )


_ROOT_TEX = r"""
\documentclass{article}
\begin{document}
\input{sections/results}
\end{document}
"""

_NESTING_SECTION_TEX = "\\input{tables/main_table}\n" + _table("Accuracy", "92.4")
_NESTED_TABLE_TEX = _table("F1", "89.1")
#: A superseded draft, left in the tarball, reachable from no document.
_ORPHAN_TEX = _table("Accuracy", "71.2")


def test_a_tex_file_no_document_reaches_is_not_read(make_evidence):
    """A source tree keeps drafts the paper does not compile.

    Their numbers appear in no rendered document, so extracting them states a
    claim the paper never made -- with the same confidence as one it did.
    """
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX,
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["paper/main.tex", "paper/sections/results.tex"]
    assert latex.main_file == "paper/main.tex"
    assert [c.value for c in latex.table_cells] == [92.4]


def test_an_include_is_followed_through_every_level(make_evidence):
    """Papers nest: a section inputs its tables, which is where the numbers are."""
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX,
            "paper/sections/results.tex": _NESTING_SECTION_TEX,
            "paper/sections/tables/main_table.tex": _NESTED_TABLE_TEX,
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == [
        "paper/main.tex",
        "paper/sections/results.tex",
        "paper/sections/tables/main_table.tex",
    ]
    assert [c.value for c in latex.table_cells] == [92.4, 89.1]


def test_a_commented_out_include_is_not_an_include(make_evidence):
    r"""``% \input{ablations}`` is how a draft is taken out of a paper."""
    latex = make_evidence(
        {
            "paper/main.tex": _ROOT_TEX.replace(
                "\\end{document}", "% \\input{ablations}\n\\end{document}"
            ),
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert "paper/ablations.tex" not in latex.tex_files
    assert not any(c.value == 71.2 for c in latex.table_cells)


def test_an_include_resolves_against_the_including_file_then_the_tree_root(make_evidence):
    """Both of LaTeX's search paths, and the implied ``.tex`` extension."""
    root = (
        "\\documentclass{article}\n"
        "\\input{tables/scores.tex}\n"
        "\\input{shared/appendix}\n"
    )
    latex = make_evidence(
        {
            "src/main.tex": root,
            "src/tables/scores.tex": _table("Accuracy", "88.1"),
            "shared/appendix.tex": _table("F1", "77.3"),
            "src/old.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["shared/appendix.tex", "src/main.tex", "src/tables/scores.tex"]
    assert sorted(c.value for c in latex.table_cells) == [77.3, 88.1]


def test_a_nested_include_resolves_against_the_root_documents_directory(make_evidence):
    """LaTeX runs in the root document's directory, so paths are relative to it.

    A section file under ``sec/`` that inputs ``tables/results`` means the
    ``tables`` directory beside the root, not one inside ``sec/``. Resolving it
    against the including file alone dropped every table such a paper keeps in
    its own directory.
    """
    latex = make_evidence(
        {
            "src/main.tex": "\\documentclass{article}\n\\input{sec/experiments}\n",
            "src/sec/experiments.tex": "\\input{tables/results.tex}\n",
            "src/tables/results.tex": _table("Accuracy", "81.2"),
            "src/old.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["src/main.tex", "src/sec/experiments.tex", "src/tables/results.tex"]
    assert [c.value for c in latex.table_cells] == [81.2]


def test_a_circular_include_terminates_and_reads_each_file_once(make_evidence):
    """Two sections inputting each other must not loop or double-count."""
    latex = make_evidence(
        {
            "paper/main.tex": "\\documentclass{article}\n\\input{a}\n",
            "paper/a.tex": "\\input{b}\n" + _table("Accuracy", "1.5"),
            "paper/b.tex": "\\input{a}\n\\input{b}\n" + _table("F1", "2.5"),
        }
    ).latex
    assert latex.tex_files == ["paper/a.tex", "paper/b.tex", "paper/main.tex"]
    assert [c.value for c in latex.table_cells] == [1.5, 2.5]


def test_a_paper_with_no_documentclass_is_read_whole(make_evidence):
    """A directory of fragments has no root to resolve, and must still report.

    Scoping to an include graph that cannot be found would turn every such
    paper into no evidence at all, which is the worse failure of the two.
    """
    latex = make_evidence(
        {
            "paper/results.tex": _table("Accuracy", "92.4"),
            "paper/ablations.tex": _ORPHAN_TEX,
        }
    ).latex
    assert latex.tex_files == ["paper/ablations.tex", "paper/results.tex"]
    assert latex.main_file is None
    assert sorted(c.value for c in latex.table_cells) == [71.2, 92.4]


def test_a_root_that_reaches_nothing_is_read_whole(make_evidence):
    """An inclusion mechanism this does not follow reads as no graph at all."""
    latex = make_evidence(
        {
            "paper/main.tex": "\\documentclass{article}\n\\subfile{sections/results}\n",
            "paper/sections/results.tex": _table("Accuracy", "92.4"),
        }
    ).latex
    assert latex.tex_files == ["paper/main.tex", "paper/sections/results.tex"]
    assert [c.value for c in latex.table_cells] == [92.4]


#: Every environment a results table is written in, with the arguments each
#: takes: a width for the two that size themselves, an optional placement for
#: ``longtable``, and a column spec whose own braces nest.
_TABLE_OPENINGS = [
    (r"\begin{tabular}{l@{\hskip 6pt}cc}", r"\end{tabular}"),
    (r"\begin{tabularx}{\textwidth}{l@{\hskip 6pt}XX}", r"\end{tabularx}"),
    (r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}lcc}", r"\end{tabular*}"),
    (r"\begin{longtable}[c]{l@{\hskip 6pt}cc}", r"\end{longtable}"),
]


@pytest.mark.parametrize(("opening", "closing"), _TABLE_OPENINGS)
def test_every_table_environment_yields_its_cells(make_evidence, opening, closing):
    """A results table is written in whichever of these the layout wanted.

    Matching the name ``tabular`` alone leaves a paper that sizes its tables to
    the text width with no tables at all.
    """
    tex = opening + "\nModel & Top-1 & F1 \\\\\nOurs & 92.4 & 89.1 \\\\\n" + closing + "\n"
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.column_label, c.value) for c in cells] == [
        ("Ours", "Top-1", 92.4),
        ("Ours", "F1", 89.1),
    ]


def test_a_longtable_is_not_closed_by_a_stray_end_tabular(make_evidence):
    r"""The environment that closes a table must be the one that opened it.

    Closing on any ``\end`` ends the table at the first environment to finish
    inside it, and every row after that point is lost.
    """
    tex = (
        "\\begin{longtable}{lcc}\n"
        "Model & Top-1 & F1 \\\\\n"
        "Ours & 92.4 & 89.1 \\\\\n"
        "\\end{tabular}\n"
        "Baseline & 90.2 & 87.0 \\\\\n"
        "\\end{longtable}\n"
    )
    cells = make_evidence({"paper/main.tex": tex}).latex.table_cells
    assert [(c.row_label, c.value) for c in cells] == [
        ("Ours", 92.4),
        ("Ours", 89.1),
        ("Baseline", 90.2),
        ("Baseline", 87.0),
    ]


def test_a_table_environments_own_arguments_are_stripped_by_brace_matching():
    r"""A width and a nested column spec are not the first row's content."""
    from adduce.evidence.latex import _table_body

    body = "{\\linewidth}{@{\\extracolsep{\\fill}}lcc}\nModel & Top-1 \\\\"
    assert _table_body("tabular*", body) == "\nModel & Top-1 \\\\"
    assert _table_body("tabular", "{p{3cm}c}\nModel & F1 \\\\") == "\nModel & F1 \\\\"
