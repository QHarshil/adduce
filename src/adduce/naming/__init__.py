"""Naming maps: import names vs distribution names, and hyperparameter synonyms.

Two mismatch problems live here:

- A Python import (``sklearn``, ``cv2``, ``PIL``) rarely matches the PyPI
  distribution that provides it (``scikit-learn``, ``opencv-python``,
  ``pillow``). Ghost-dependency detection needs the mapping in both
  directions.
- The same hyperparameter appears under different names in papers, configs,
  and CLIs (``lr`` / ``learning_rate`` / "learning rate"). Drift detection
  normalises through the synonym map before comparing.
"""

from __future__ import annotations

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


def dist_for_import(module_root: str) -> str:
    """The distribution likely providing an import (identity when unmapped)."""
    return IMPORT_TO_DIST.get(module_root, module_root.lower().replace("_", "-"))
