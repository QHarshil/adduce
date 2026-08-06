"""One read per file, shared by every collector that wants it.

Three collectors used to walk the repository independently — the AST analysis,
the portability line scan, and the remote-reference line scan — so every Python
file was opened, decoded from UTF-8, and split into lines three times. Measured
on the largest corpus repository: 15,583 reads over 6,246 files, 4,648 of them
read exactly three times.

Caching the text instead does not fix it. The passes are sequential and each
covers the whole repository, so any cache smaller than the working set is
evicted before the second pass reaches it — which is exactly what the previous
512-entry cache did, at a 0.3% hit rate. Holding everything is not available
either: the decoded Python source of the largest corpus repository is 135 MB,
and the point of this work is to bring peak memory down.

So the read is shared in time rather than in memory. The inventory is walked
once, each file is read once, its text is handed to every consumer that wants
it, and it is released before the next file is read. Peak text held is one file.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .model import FileEntry, Repo
from .telemetry import Telemetry

#: Set to 1 to make a second read of the same path within one pass an error
#: rather than a silent regression. ``bench/`` runs with it on.
DEBUG_STRICT_ENV = "ADDUCE_DEBUG_STRICT"


@runtime_checkable
class FileConsumer(Protocol):
    """A collector that reads source files line by line or as a whole.

    Consumers are pure with respect to each other: the shared pass promises only
    that ``feed`` is called once per wanted file, in inventory order, which is
    the order each collector walked the repository in before.
    """

    def wants(self, entry: FileEntry) -> bool:
        """Whether this consumer needs the text of ``entry``."""

    def feed(self, entry: FileEntry, text: str) -> None:
        """Accept one file's decoded text."""


class DuplicateReadError(RuntimeError):
    """A file was read twice in one pass while strict debugging was enabled."""


def scan_once(
    repo: Repo,
    consumers: list[FileConsumer],
    *,
    telemetry: Telemetry | None = None,
) -> None:
    """Walk the inventory once, giving each wanted file to every consumer.

    A file no consumer wants is never opened. A file that cannot be decoded is
    skipped for everyone, which matches what each collector did on its own —
    they all treated an undecodable file as absent.
    """
    strict = os.environ.get(DEBUG_STRICT_ENV) == "1"
    seen: set[str] = set()

    for entry in repo.files:
        wanted = [consumer for consumer in consumers if consumer.wants(entry)]
        if not wanted:
            continue
        relative = str(entry.path)
        if strict:
            if relative in seen:
                raise DuplicateReadError(
                    f"{relative} was read twice in one pass; the shared read is not shared"
                )
            seen.add(relative)
        text = repo.read_text(relative)
        if text is None:
            continue
        if telemetry is not None:
            telemetry.count("files.shared_reads")
        for consumer in wanted:
            consumer.feed(entry, text)
