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
    """Map a config key or paper phrase to its canonical hyperparameter, if known."""
    key = name.strip().lower()
    if key in HYPERPARAM_SYNONYMS:
        return HYPERPARAM_SYNONYMS[key]
    # Dotted config keys resolve on their terminal segment (optim.lr -> lr).
    terminal = key.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
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
    ("accuracy", "acc", "acc.", "top-1", "top1", "top-1 acc", "top-5", "top5", "top-5 acc", "err", "error rate"),
    ("f1", "f-1", "f1-score", "f score", "f-score", "macro-f1", "micro-f1", "macro f1", "micro f1"),
    ("bleu", "bleu-4", "bleu4", "sacrebleu"),
    ("rouge", "rouge-l", "rouge-1", "rouge-2", "rougel"),
    ("ndcg", "ndcg@10", "ndcg@5"),
    ("map", "mean average precision", "ap", "ap50", "ap75", "ap@50"),
    ("mrr", "mean reciprocal rank"),
    ("auc", "auroc", "roc-auc", "auc-roc", "aupr", "auprc"),
    ("precision", "prec", "prec.", "precision@k"),
    ("recall", "rec", "rec.", "recall@k"),
    ("perplexity", "ppl", "perp"),
    ("wer", "word error rate", "cer"),
    ("mse", "mean squared error", "l2 loss"),
    ("rmse", "root mean squared error"),
    ("mae", "mean absolute error", "l1 loss"),
    ("iou", "miou", "mean iou", "jaccard"),
    ("dice", "dice score", "dsc"),
    ("exact_match", "exact match", "em"),
    ("spearman", "spearman correlation", "spearman's rho", "rho"),
    ("pearson", "pearson correlation", "pearson's r"),
    # Loss is the reported result in a language-modelling repository as often
    # as accuracy is in a classification one. The splits stay distinct: a
    # training loss and a validation loss are two claims, not one restated.
    ("loss", "final loss", "objective"),
    ("train_loss", "train loss", "training loss", "trn loss", "loss (train)"),
    ("val_loss", "val loss", "validation loss", "valid loss", "dev loss", "loss (val)"),
    ("test_loss", "test loss", "eval loss", "evaluation loss", "loss (test)"),
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
    if key in METRIC_SYNONYMS:
        return METRIC_SYNONYMS[key]
    return METRIC_SYNONYMS.get(key.rsplit("/", 1)[-1].strip())


def dist_for_import(module_root: str) -> str:
    """The distribution likely providing an import (identity when unmapped)."""
    return IMPORT_TO_DIST.get(module_root, module_root.lower().replace("_", "-"))
