"""The page-text dump must stay searchable: no control characters, no NUL byte.

A NUL byte in the dump is a labelling-integrity defect, not a cosmetic one. One
is enough for a search tool to classify the file as binary and stop reporting
matching lines, and a labeller who searches for a value that is printed on the
page then reads absence and can record ``not_in_paper`` -- the verdict
``bench/dev/README.md`` asks adjudicators to be surest about. It was hit for
real: detr's ``\\big(``/``\\big)`` extract as U+0000 and U+0001.

PyMuPDF is deliberately not a project dependency, so ``_open`` is substituted by
a stub document here. The sanitiser itself is a pure function and is tested
directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bench.dev import paper_text


class _StubPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _StubDocument:
    """The subset of a PyMuPDF document ``dump_text`` uses: iteration and length."""

    def __init__(self, texts: list[str]) -> None:
        self._pages = [_StubPage(text) for text in texts]

    def __iter__(self):
        return iter(self._pages)

    def __len__(self) -> int:
        return len(self._pages)


@pytest.fixture
def stub_document(monkeypatch: pytest.MonkeyPatch):
    def install(texts: list[str]) -> None:
        monkeypatch.setattr(paper_text, "_open", lambda pdf: _StubDocument(texts))

    return install


def test_sanitiser_replaces_the_nul_byte_rather_than_dropping_it() -> None:
    # The exact shape detr produces: an unmappable delimiter either side of a
    # subexpression.
    cleaned = paper_text.sanitise_extracted_text("layernorm\x00Xq + dropout(LX)\x01")
    assert "\x00" not in cleaned
    marker = paper_text.UNMAPPABLE_GLYPH
    assert cleaned == f"layernorm{marker}Xq + dropout(LX){marker}"


def test_sanitiser_never_joins_two_numbers_into_a_third() -> None:
    """Why replacement and not deletion: deleting would print a value no page states."""
    cleaned = paper_text.sanitise_extracted_text("9\x003")
    assert "93" not in cleaned
    assert cleaned == f"9{paper_text.UNMAPPABLE_GLYPH}3"


def test_sanitiser_replaces_c0_and_c1_controls_and_keeps_layout_whitespace() -> None:
    controls = "".join(chr(code) for code in (*range(0x00, 0x20), *range(0x7F, 0xA0)))
    cleaned = paper_text.sanitise_extracted_text(controls)
    assert set(cleaned) == {paper_text.UNMAPPABLE_GLYPH, "\t", "\n", "\r"}
    assert cleaned.count("\t") == 1
    assert cleaned.count("\n") == 1
    assert cleaned.count("\r") == 1


def test_sanitiser_leaves_printable_text_alone() -> None:
    # U+00A0 is a separator, not a control, and appears in real page text; the
    # ligature is what extraction produces for "fi". Neither may be rewritten.
    original = "42.0 AP on COCO, ﬁnd Table 1\ttab\nline"
    assert paper_text.sanitise_extracted_text(original) == original


def test_dump_text_writes_no_control_character(tmp_path: Path, stub_document) -> None:
    stub_document(["clean page\n", "map \x0042.0\x01 AP\n"])
    destination = tmp_path / "nested" / "pair.txt"

    assert paper_text.dump_text(tmp_path / "paper.pdf", destination) == 2

    written = destination.read_bytes()
    assert b"\x00" not in written
    text = destination.read_text(encoding="utf-8")
    assert "42.0" in text
    controls = [c for c in text if c not in "\t\n\r" and ord(c) < 0x20]
    assert controls == []
    # The page delimiters the labeller navigates by are still there.
    assert text.count("PAGE 1 of 2") == 1
    assert text.count("PAGE 2 of 2") == 1


def test_dump_text_output_is_searchable_line_by_line(tmp_path: Path, stub_document) -> None:
    """The failure was a whole-file property, so assert the whole-file property."""
    stub_document(["\x00layernorm\nAP 42.0\n"])
    destination = tmp_path / "pair.txt"
    paper_text.dump_text(tmp_path / "paper.pdf", destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if "42.0" in line] == ["AP 42.0"]
