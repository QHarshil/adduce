"""Naming maps: import names vs distribution names, and hyperparameter synonyms.

Two mismatch problems live here:

- A Python import (``sklearn``, ``cv2``, ``PIL``) rarely matches the PyPI
  distribution that provides it (``scikit-learn``, ``opencv-python``,
  ``pillow``). Ghost-dependency detection needs the mapping in both
  directions.
- The same hyperparameter appears under different names in papers, configs,
  and CLIs (``lr`` / ``learning_rate`` / "learning rate"). Drift detection
  normalises through the synonym map before comparing.

The same problem applies to metrics — ``Top-1``, ``Acc.`` and "accuracy" are
one metric stated three ways — so the metric vocabulary lives here too, shared
by the LaTeX collector and by claim extraction from tables and result files.
"""

from __future__ import annotations

import re

#: import name -> distribution name, where they differ (lowercased).
IMPORT_TO_DIST: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "pil": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "attrs": "attrs",
    "git": "gitpython",
    "fitz": "pymupdf",
    "OpenSSL": "pyopenssl",
    "openssl": "pyopenssl",
    "serial": "pyserial",
    "wx": "wxpython",
    "Levenshtein": "python-levenshtein",
    "levenshtein": "python-levenshtein",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "magic": "python-magic",
    "gi": "pygobject",
    "lightning": "lightning",
    "pytorch_lightning": "pytorch-lightning",
    "pl": "pytorch-lightning",
    "tensorboard": "tensorboard",
    "torchvision": "torchvision",
    "jose": "python-jose",
    "cairosvg": "cairosvg",
    "kaggle": "kaggle",
    "graphviz": "graphviz",
    "faiss": "faiss-cpu",
    "sentence_transformers": "sentence-transformers",
    "flash_attn": "flash-attn",
    "ruamel": "ruamel.yaml",
    "typing_extensions": "typing-extensions",
    "pkg_resources": "setuptools",
    "setuptools": "setuptools",
    "google": "google",  # namespace package; unreliable, treated leniently
    "ml_collections": "ml-collections",
    "simple_parsing": "simple-parsing",
    "memory_profiler": "memory-profiler",
    "gdown": "gdown",
}

#: Python standard library top-level modules (never a missing dependency).
#: Kept explicit rather than importing sys.stdlib_module_names so behaviour
#: does not vary with the interpreter running the scan.
STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "abc", "argparse", "array", "ast", "asyncio", "atexit", "base64", "bisect",
        "builtins", "bz2", "calendar", "cmath", "cmd", "collections", "concurrent",
        "configparser", "contextlib", "contextvars", "copy", "copyreg", "csv",
        "ctypes", "dataclasses", "datetime", "decimal", "difflib", "dis", "doctest",
        "email", "enum", "errno", "faulthandler", "filecmp", "fileinput", "fnmatch",
        "fractions", "ftplib", "functools", "gc", "getopt", "getpass", "gettext",
        "glob", "graphlib", "gzip", "hashlib", "heapq", "hmac", "html", "http",
        "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword",
        "linecache", "locale", "logging", "lzma", "mailbox", "math", "mimetypes",
        "mmap", "multiprocessing", "netrc", "numbers", "operator", "os", "pathlib",
        "pdb", "pickle", "pickletools", "pkgutil", "platform", "plistlib", "poplib",
        "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
        "pyclbr", "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
        "resource", "runpy", "sched", "secrets", "select", "selectors", "shelve",
        "shlex", "shutil", "signal", "site", "smtplib", "socket", "socketserver",
        "sqlite3", "ssl", "stat", "statistics", "string", "stringprep", "struct",
        "subprocess", "symtable", "sys", "sysconfig", "tarfile", "tempfile",
        "termios", "textwrap", "threading", "time", "timeit", "tkinter", "token",
        "tokenize", "tomllib", "traceback", "tracemalloc", "tty", "turtle", "types",
        "typing", "unicodedata", "unittest", "urllib", "uuid", "venv", "warnings",
        "wave", "weakref", "webbrowser", "wsgiref", "xml", "xmlrpc", "zipapp",
        "zipfile", "zipimport", "zlib", "zoneinfo", "__future__",
    }
)

#: Hyperparameter synonym groups. The first entry is the canonical name.
_HYPERPARAM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("learning_rate", "lr", "learning rate", "base_lr", "initial_lr", "eta", "step_size_lr"),
    ("batch_size", "bs", "batch size", "train_batch_size", "per_device_train_batch_size", "batchsize"),
    ("epochs", "num_epochs", "n_epochs", "max_epochs", "num_train_epochs", "training epochs"),
    ("steps", "max_steps", "num_steps", "total_steps", "iterations", "num_iterations", "max_iter", "iters"),
    ("weight_decay", "wd", "weight decay", "l2", "l2_reg", "l2_regularization"),
    ("dropout", "dropout_rate", "drop_rate", "dropout_prob", "p_dropout"),
    ("seed", "random_seed", "random_state", "rng_seed"),
    ("hidden_size", "hidden_dim", "d_model", "hidden size", "hidden dimension", "embed_dim", "embedding_dim"),
    ("num_layers", "n_layers", "layers", "num_hidden_layers", "depth"),
    ("num_heads", "n_heads", "heads", "num_attention_heads", "attention heads"),
    ("warmup_steps", "num_warmup_steps", "warmup", "warmup_ratio"),
    ("temperature", "temp", "tau"),
    ("momentum", "beta1", "beta_1"),
    ("beta2", "beta_2"),
    ("gradient_clip", "grad_clip", "max_grad_norm", "clip_grad_norm", "gradient clipping"),
    ("optimizer", "optim", "opt"),
    ("scheduler", "lr_scheduler", "lr_schedule", "schedule"),
    ("label_smoothing", "label smoothing", "smoothing"),
    ("top_k", "topk", "k"),
    ("num_workers", "workers", "n_workers"),
)

#: any-alias -> canonical hyperparameter name (keys lowercased, underscores kept).
HYPERPARAM_SYNONYMS: dict[str, str] = {}
for _group in _HYPERPARAM_GROUPS:
    _canonical = _group[0]
    for _alias in _group:
        HYPERPARAM_SYNONYMS[_alias.lower()] = _canonical
        HYPERPARAM_SYNONYMS[_alias.lower().replace(" ", "_")] = _canonical


def canonical_hyperparameter(name: str) -> str | None:
    """Map a config key or paper phrase to its canonical hyperparameter, if known.

    The terminal segment is stripped as the whole name is. A separator followed
    by a space is how a paper abbreviates rather than how a config nests, so
    ``dec. depth`` -- MAE's own ablation column -- split to `` depth``, which
    resolved to nothing where ``depth`` resolves to ``num_layers``: one word
    named a hyperparameter or named none according to a character that is no
    part of it, and a decoder depth a config stated outright was reported as
    having no counterpart in code.

    Measured over the twenty labelled dev pairs this changes nothing: 3,839
    lookups over 1,090 distinct keys, from config files, materialised run
    configs, command-line arguments and dataclass fields, and none of them
    resolves differently. What exercises it is
    ``corpus/synthetic/synthetic_spaced_config_key``.
    """
    key = name.strip().lower()
    if key in HYPERPARAM_SYNONYMS:
        return HYPERPARAM_SYNONYMS[key]
    # Dotted config keys resolve on their terminal segment (optim.lr -> lr).
    terminal = key.rsplit(".", 1)[-1].rsplit("/", 1)[-1].strip()
    return HYPERPARAM_SYNONYMS.get(terminal)


#: Canonical metric -> the regex alternatives that name it in paper prose.
#: These are *patterns*, not literals: several carry word boundaries to stop
#: "map" matching inside "mapping" or "em" inside "them". Moved here from
#: ``evidence.latex`` so a markdown table, a result column and a LaTeX sentence
#: canonicalise the same metric to the same name; the collector still owns how
#: it applies them.
METRIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "accuracy": ("accuracy", "acc\\.", "top-1", "top-5"),
    "f1": ("f1", "f1-score", "f-score", "macro-f1", "micro-f1"),
    "bleu": ("bleu",),
    "rouge": ("rouge", "rouge-l", "rouge-1", "rouge-2"),
    "ndcg": ("ndcg",),
    "map": ("\\bmap\\b", "mean average precision"),
    "mrr": ("mrr", "mean reciprocal rank"),
    "auc": ("auc", "auroc", "roc-auc"),
    "precision": ("precision@", "\\bprecision\\b"),
    "recall": ("recall@", "\\brecall\\b"),
    "perplexity": ("perplexity", "\\bppl\\b"),
    "wer": ("\\bwer\\b", "word error rate"),
    "mse": ("\\bmse\\b", "mean squared error"),
    "rmse": ("\\brmse\\b",),
    "mae": ("\\bmae\\b", "mean absolute error"),
    "iou": ("\\biou\\b", "\\bmiou\\b"),
    "dice": ("dice",),
    "exact_match": ("exact match", "\\bem\\b"),
}

#: Metric synonym groups for *literal* lookup — a table header or a result
#: column, where there is no surrounding prose to disambiguate and so no need
#: for boundary syntax. The first entry is the canonical name.
_METRIC_GROUPS: tuple[tuple[str, ...], ...] = (
    # "top-1 acc" was listed but "top-1 accuracy" was not, so the most ordinary
    # spelling of the most common metric in the set resolved to nothing.
    (
        "accuracy",
        "acc",
        "acc.",
        "acc1",
        "acc5",
        "top-1",
        "top1",
        "top-1 acc",
        "top-1 acc.",
        "top-1 accuracy",
        "top1 accuracy",
        "top-5",
        "top5",
        "top-5 acc",
        "top-5 acc.",
        "top-5 accuracy",
        "top5 accuracy",
    ),
    # Error rate is the complement of accuracy, not a synonym for it. Aliasing
    # the two made a reported "error rate 15.5" and an accuracy of 15.5 the same
    # claim, and a reported error rate and a matching accuracy look like a
    # disagreement.
    ("error_rate", "err", "err.", "error", "error rate", "top-1 err", "top-1 error"),
    ("f1", "f-1", "f1-score", "f score", "f-score", "macro-f1", "micro-f1", "macro f1", "micro f1"),
    ("bleu", "bleu-4", "bleu4", "sacrebleu", "b@4"),
    # ROUGE-1, ROUGE-2 and ROUGE-L are three summarisation metrics printed side
    # by side in one row, so they are separated for the reason AP50 and AP75
    # are. The bare name stays for a paper reporting one and not saying which.
    # The ``-F`` suffix names the F-measure of the same variant.
    ("rouge",),
    ("rouge_1", "rouge-1", "rouge1", "rouge-1-f", "r-1-f"),
    ("rouge_2", "rouge-2", "rouge2", "rouge-2-f", "r-2-f"),
    ("rouge_l", "rouge-l", "rougel", "rouge-l-f", "r-l-f"),
    ("ndcg", "ndcg@10", "ndcg@5"),
    # COCO's bare "AP" is already averaged over IoU thresholds, so it belongs
    # with mAP. AP50 and AP75 are that same average taken at one fixed
    # threshold: different numbers, reported side by side in the same row, so
    # collapsing them onto "map" turned one row into several claims about one
    # metric with different values -- which reads as a contradiction.
    ("map", "mean average precision", "ap", "average precision"),
    ("ap50", "ap@50", "ap_50", "ap 50", "ap@0.5"),
    ("ap75", "ap@75", "ap_75", "ap 75", "ap@0.75"),
    # Detection and segmentation papers report box and mask AP side by side, so
    # they are separate metrics for the same reason AP50 and AP75 are.
    ("box_ap", "apbb", "ap^box", "ap box", "apbox"),
    ("box_ap50", "apbb50", "ap^box_50", "apbox50"),
    ("box_ap75", "apbb75", "ap^box_75", "apbox75"),
    ("mask_ap", "apmk", "ap^mask", "ap mask", "apmask"),
    ("mask_ap50", "apmk50", "ap^mask_50", "apmask50"),
    ("mask_ap75", "apmk75", "ap^mask_75", "apmask75"),
    ("mrr", "mean reciprocal rank"),
    ("auc", "auroc", "roc-auc", "auc-roc", "aupr", "auprc"),
    ("precision", "prec", "prec.", "precision@k"),
    ("recall", "rec", "rec.", "recall@k"),
    ("perplexity", "ppl", "perp"),
    # Word and character error rate are measured over different units and are
    # reported side by side; they are not one metric.
    ("wer", "word error rate"),
    ("cer", "character error rate"),
    ("mse", "mean squared error", "l2 loss"),
    ("rmse", "root mean squared error"),
    ("mae", "mean absolute error", "l1 loss"),
    ("iou", "miou", "mean iou", "jaccard"),
    ("dice", "dice score", "dsc"),
    ("exact_match", "exact match", "em"),
    # Generative and speech metrics. Measured on ten real papers, these were
    # among the most common headers the vocabulary could not name at all --
    # FID alone appeared 43 times -- and an unnamed metric is a silent recall
    # zero, the same shape as the miss that hid nanogpt's claims until "loss"
    # was added.
    ("fid", "fid score", "frechet inception distance", "fréchet inception distance"),
    ("inception_score", "inception score", "is"),
    ("lpips", "perceptual distance"),
    ("psnr",),
    ("ssim",),
    ("nll", "negative log-likelihood", "negative log likelihood"),
    ("gflops", "flops", "gflop"),
    ("spearman", "spearman correlation", "spearman's rho", "rho", "scc"),
    ("pearson", "pearson correlation", "pearson's r"),
    # Loss is the reported result in a language-modelling repository as often
    # as accuracy is in a classification one. The splits stay distinct: a
    # training loss and a validation loss are two claims, not one restated.
    ("loss", "final loss", "objective"),
    ("train_loss", "train loss", "training loss", "trn loss", "loss (train)"),
    ("val_loss", "val loss", "validation loss", "valid loss", "dev loss", "loss (val)"),
    ("test_loss", "test loss", "eval loss", "evaluation loss", "loss (test)"),
    # Cost, not quality. Measured over 20 labelled paper/code pairs these are
    # the names the papers print that the vocabulary could not read at all, so
    # every one of them was a recall zero by construction rather than an
    # extraction failure. A rate of inference is reported as a throughput, as
    # frames per second, or as a latency, and a paper printing two of them in
    # one row means two different measurements, so they stay separate.
    (
        "throughput",
        "image throughput",
        "inference throughput",
        "generation throughput",
        "im/s",
        "im/sec",
        "images/s",
        "images/sec",
        "img/s",
        "imgs/s",
    ),
    ("fps", "frames per second"),
    ("latency", "inference latency"),
    ("speedup", "speed-up", "speed up", "wall-clock speedup"),
    (
        "training_time",
        "hours",
        "gpu hours",
        "gpu-hours",
        "training time",
        "train time",
        "pre-training time",
        "pretraining time",
    ),
    ("peak_memory", "peak memory", "peak memory per gpu", "peak mem"),
    # A count of parameters, kept apart from the bare ``params`` a README
    # argument table heads a column of option names with -- that one stays
    # unnamed deliberately, and the sigil is what distinguishes them.
    ("param_count", "#params", "#param", "#param."),
    # Captioning and translation.
    ("cider", "cider-d", "ciderd"),
    ("spice",),
    ("meteor", "met"),
    ("ter", "translation edit rate"),
    ("vqa_score", "vqa score"),
    # Retrieval reports recall at several ranks in one row, and text-to-image
    # and image-to-text retrieval in adjacent column groups: four numbers that
    # differ, so four metrics.
    ("recall_at_1", "r@1", "recall@1"),
    ("recall_at_5", "r@5", "recall@5"),
    ("recall_at_10", "r@10", "recall@10"),
    ("text_recall_at_1", "tr@1"),
    ("image_recall_at_1", "ir@1"),
    ("average_recall_at_1", "average recall@1"),
    # AP at one object scale, and AP75 for keypoint and dense-pose tasks:
    # separate from AP for the reason AP50 and AP75 are separate from it.
    ("ap_large", "apl", "ap_l", "ap^l"),
    ("keypoint_ap75", "apkp75", "ap^kp_75"),
    ("densepose_ap75", "apdp75", "ap^dp_75"),
    # Matthews correlation is CoLA's metric; the dataset name itself is not a
    # metric and is deliberately absent.
    ("matthews", "mcc", "matthews correlation", "matthews correlation coefficient"),
    # Linear probing and end-to-end fine-tuning are two protocols evaluated in
    # adjacent columns of one table, so a linear-probe accuracy is its own
    # metric rather than an accuracy.
    ("linear_probe_accuracy", "lin", "lin.", "linear probe", "linear probing"),
)

#: The names a dataset split goes by when a table header puts it in front of the
#: metric: "dev F1", "test EM". A split is not itself a metric, so a header that
#: is only a split names none.
SPLIT_WORDS: frozenset[str] = frozenset(
    {"train", "training", "dev", "development", "val", "valid", "validation", "test", "eval",
     "evaluation"}
)

#: any-alias -> canonical metric name (keys lowercased).
METRIC_SYNONYMS: dict[str, str] = {}
for _mgroup in _METRIC_GROUPS:
    _mcanonical = _mgroup[0]
    for _malias in _mgroup:
        METRIC_SYNONYMS[_malias.lower()] = _mcanonical
        METRIC_SYNONYMS[_malias.lower().replace(" ", "_")] = _mcanonical


def canonical_metric(name: str) -> str | None:
    """Map a table header or result column to its canonical metric, if known.

    Literal lookup only. Callers matching inside prose want
    :data:`METRIC_PATTERNS`, whose entries carry the boundary syntax that stops
    ``map`` matching inside ``mapping``.
    """
    key = name.strip().lower().strip("*_`")
    # Headers routinely carry a unit or a qualifier: "Accuracy (%)", "F1 ↑".
    key = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", key).strip()
    key = key.rstrip("↑↓*").strip()
    # Whole name first, then after a slash qualifier ("GLUE/MNLI acc").
    for candidate in (key, key.rsplit("/", 1)[-1].strip()):
        whole = METRIC_SYNONYMS.get(candidate)
        if whole is not None:
            return whole
    # A header commonly qualifies the metric with the split or the dataset it
    # was measured on -- "dev F1", "RACE accuracy", "SQuAD1.1 EM". The qualifier
    # is not the metric, so fall back to the trailing words, accepting them only
    # when they name a metric outright. Matching the whole name first is what
    # keeps a deliberately compound metric -- "train loss", "word error rate",
    # "mean average precision" -- from being flattened onto its last word.
    tokens = key.split()
    for size in (2, 1):
        if len(tokens) > size:
            tail = METRIC_SYNONYMS.get(" ".join(tokens[-size:]))
            if tail is not None:
                return tail
    return None


def dist_for_import(module_root: str) -> str:
    """The distribution likely providing an import (identity when unmapped)."""
    return IMPORT_TO_DIST.get(module_root, module_root.lower().replace("_", "-"))
