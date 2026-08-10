"""The shared read pass: one read per file, and identical results.

Three collectors used to walk the repository independently, so every Python file
was opened and decoded three times. They now receive the text as it is read. The
risk in that change is not performance but silence: a consumer that stops being
fed, or is fed in a different order, would change findings without failing
anything obvious. These pin both halves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adduce.content import DEBUG_STRICT_ENV, DuplicateReadError, scan_once
from adduce.evidence import collect
from adduce.evidence.portability import PortabilityConsumer, collect_portability
from adduce.evidence.python_ast import PythonConsumer, collect_python
from adduce.evidence.remote import RemoteEvidence, RemoteTextConsumer, collect_remote
from adduce.model import FileEntry, Repo, scan_repository

_REPO = {
    "train.py": (
        "import torch\n"
        "import requests\n\n"
        "def train():\n"
        "    torch.manual_seed(0)\n"
        "    requests.get('https://example.org/weights.bin')\n"
        "    path = '/Users/someone/data'\n"
        "    return path\n"
    ),
    "fetch.sh": "curl https://example.org/model.bin -o model.bin\n",
    "config.yaml": "learning_rate: 0.0003\nendpoint: http://localhost:8080\n",
    "notes.md": "See /home/alice/notes for detail.\n",
}


def _build(root: Path) -> Repo:
    for name, content in _REPO.items():
        (root / name).write_bytes(content.encode("utf-8"))
    return scan_repository(root)


class _Recorder:
    """A consumer that records what it was handed, in order."""

    def __init__(self, suffixes: set[str]) -> None:
        self.suffixes = suffixes
        self.seen: list[str] = []

    def wants(self, entry: FileEntry) -> bool:
        return entry.suffix in self.suffixes

    def feed(self, entry: FileEntry, text: str) -> None:
        self.seen.append(str(entry.path))


def _build_counting_reads(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Repo, list[str]]:
    """Build a repo whose disk reads are recorded.

    The patch has to precede construction: ``Repo`` binds its cached reader in
    ``__post_init__``, so patching the class afterwards leaves the bound
    original in place and the count silently reads zero.
    """
    reads: list[str] = []
    original = Repo._read_uncached

    def counted(self: Repo, relative: str) -> str | None:
        reads.append(relative)
        return original(self, relative)

    monkeypatch.setattr(Repo, "_read_uncached", counted)
    return _build(root), reads


def test_each_wanted_file_is_read_once_and_given_to_every_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, reads = _build_counting_reads(tmp_path, monkeypatch)

    python = _Recorder({".py"})
    shell = _Recorder({".py", ".sh"})
    broad = _Recorder({".py", ".sh", ".yaml", ".md"})
    scan_once(repo, [python, shell, broad])

    assert python.seen == ["train.py"]
    assert shell.seen == ["fetch.sh", "train.py"]
    assert broad.seen == ["config.yaml", "fetch.sh", "notes.md", "train.py"]
    # Four files wanted by at least one consumer, and four reads: the three
    # consumers between them asked for train.py three times.
    assert sorted(reads) == ["config.yaml", "fetch.sh", "notes.md", "train.py"]


def test_a_file_no_consumer_wants_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, reads = _build_counting_reads(tmp_path, monkeypatch)

    scan_once(repo, [_Recorder({".py"})])

    assert reads == ["train.py"]


def test_strict_mode_rejects_a_second_read_of_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard has to be able to fire, or it records nothing."""
    repo = _build(tmp_path)
    monkeypatch.setenv(DEBUG_STRICT_ENV, "1")
    repo.files = list(repo.files) + list(repo.files)

    with pytest.raises(DuplicateReadError, match="was read twice"):
        scan_once(repo, [_Recorder({".py", ".sh", ".yaml", ".md"})])


def test_a_repeated_file_is_tolerated_when_strict_mode_is_off(tmp_path: Path) -> None:
    """Only the debug guard is strict; a normal run must not be brittle."""
    repo = _build(tmp_path)
    repo.files = list(repo.files) + list(repo.files)

    recorder = _Recorder({".py"})
    scan_once(repo, [recorder])

    assert recorder.seen == ["train.py", "train.py"]


def test_the_consumers_agree_with_their_standalone_collectors(tmp_path: Path) -> None:
    """The shared pass and an independent walk must produce the same evidence.

    Both paths stay in the codebase — plugins and tests call the standalone
    collectors — so they have to be kept in step, and this is what notices.
    """
    repo = _build(tmp_path)

    python_consumer = PythonConsumer()
    portability_consumer = PortabilityConsumer()
    remote_consumer = RemoteTextConsumer(RemoteEvidence())
    scan_once(repo, [python_consumer, remote_consumer, portability_consumer])

    shared_python = python_consumer.finish()
    standalone_python = collect_python(repo)
    assert [m.path for m in shared_python.modules] == [
        m.path for m in standalone_python.modules
    ]
    assert shared_python.imports == standalone_python.imports

    assert portability_consumer.evidence.hits == collect_portability(repo).hits

    standalone_remote = collect_remote(repo, standalone_python)
    shared_remote = RemoteEvidence()
    scanned = remote_consumer.evidence
    from adduce.evidence.remote import complete_remote

    shared_remote = complete_remote(shared_python, scanned)
    assert shared_remote.references == standalone_remote.references


def test_the_shared_pass_finds_what_the_collectors_are_supposed_to_find(
    tmp_path: Path,
) -> None:
    """A guard against all three consumers silently receiving nothing."""
    evidence = collect(_build(tmp_path))

    assert evidence.py.modules, "no Python module was analysed"
    assert evidence.portability.of_kind("abs_path"), "no absolute path was found"
    assert evidence.portability.of_kind("localhost"), "no localhost endpoint was found"
    assert evidence.remote.references, "no remote reference was found"
