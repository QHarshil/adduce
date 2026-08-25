"""Documentation indexes that are maintained by hand, and so drift by hand.

The contribution guide asks a contributor to add a decision record to the index
table in the same pull request, and to link a new page from the documentation
index. Neither is enforced anywhere: a record that never reaches the table is
invisible to every reader who starts at the index, and the link checker cannot
help because an unlinked page has no broken link.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ADR = DOCS / "adr"
ADR_INDEX = ADR / "0000-index.md"

#: ``| [0001](0001-slug.md) | Title | Status |``
_INDEX_ROW = re.compile(
    r"^\|\s*\[(?P<number>\d{4})\]\((?P<target>[^)]+)\)\s*\|(?P<title>[^|]*)\|\s*(?P<status>[^|]+?)\s*\|",
    re.MULTILINE,
)
#: ``- **Status:** Accepted``
_RECORD_STATUS = re.compile(r"^-\s+\*\*Status:\*\*\s*(?P<status>.+?)\s*$", re.MULTILINE)
_RECORD_DATE = re.compile(r"^-\s+\*\*Date:\*\*\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def _rows() -> list[re.Match[str]]:
    return list(_INDEX_ROW.finditer(ADR_INDEX.read_text(encoding="utf-8")))


def _record_files() -> list[Path]:
    return sorted(p for p in ADR.glob("*.md") if p.name != ADR_INDEX.name)


def test_every_decision_record_appears_in_the_index():
    listed = {m.group("target") for m in _rows()}
    on_disk = {p.name for p in _record_files()}
    assert on_disk - listed == set(), "record on disk but missing from the index table"
    assert listed - on_disk == set(), "index table row pointing at no record"


def test_the_index_numbers_each_record_the_way_the_record_numbers_itself():
    for match in _rows():
        record = ADR / match.group("target")
        heading = record.read_text(encoding="utf-8").splitlines()[0]
        assert heading.startswith(f"# {int(match.group('number'))}. "), record.name


def test_no_record_number_is_used_twice():
    numbers = [m.group("number") for m in _rows()]
    assert len(numbers) == len(set(numbers))
    assert numbers == sorted(numbers), "index rows are not in record order"


def test_the_status_in_the_index_matches_the_status_in_the_record():
    """A record superseded in its own file but still listed as accepted is the
    drift this catches: the index is what a reader trusts."""
    for match in _rows():
        record = ADR / match.group("target")
        status = _RECORD_STATUS.search(record.read_text(encoding="utf-8"))
        assert status is not None, f"{record.name} states no status"
        assert status.group("status") == match.group("status"), record.name


def test_every_record_carries_a_status_and_a_date():
    for record in _record_files():
        text = record.read_text(encoding="utf-8")
        assert _RECORD_STATUS.search(text) is not None, record.name
        assert _RECORD_DATE.search(text) is not None, record.name


def test_every_documentation_page_is_reachable_from_the_index():
    """An unlinked page has no broken link, so the link checker cannot see it."""
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    pages = {p.name for p in DOCS.glob("*.md")} - {"index.md"}
    unlinked = {name for name in pages if f"({name})" not in index}
    assert unlinked == set(), f"documentation pages linked from nowhere: {sorted(unlinked)}"


def test_the_decision_records_are_reachable_from_the_documentation_index():
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    assert "adr/0000-index.md" in index
