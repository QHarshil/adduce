#!/usr/bin/env python3
"""Render a dev-set paper for labelling: page text, and pages as images.

Ground truth is labelled from the rendered paper, never from the LaTeX source
(see ``bench/dev/README.md``). Both outputs here are that rendering: extracted
page text is the typeset output, with ``\\multicolumn``, ``\\rotatebox``, macros
and ``\\input`` already resolved, so it carries none of the blind spots a
``.tex`` parse shares with the extractor under test.

Text is the cheap surface and is enough for enumerating the frame, for prose
numbers and for figure captions. It loses column alignment, so attributing a
value to the right row and column needs the page image; render only the pages
that carry cells actually being transcribed.

``page.find_tables()`` was evaluated for structured cell extraction and is not
used: on the academic PDFs in this set it returns mostly empty and merged cells,
which would silently mis-attribute values.

Requires PyMuPDF, which is not a project dependency because nothing shipped
needs it:

    python -m pip install pymupdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEV_DIR = Path(__file__).resolve().parent
DEFAULT_PAIRS_ROOT = _DEV_DIR / "pairs"

#: Text extraction renders "fi" as the ligature "ﬁ" ("ﬁnd", "ﬁg"). It never
#: affects digits, but a labeller searching for a word should know.
LIGATURE_NOTE = "extracted text renders 'fi' as the ligature 'fi'; digits are unaffected"


def _open(pdf: Path):
    try:
        import fitz
    except ImportError:  # pragma: no cover - depends on the local environment
        raise SystemExit(
            "PyMuPDF is required for paper rendering: python -m pip install pymupdf"
        ) from None
    if not pdf.is_file():
        raise SystemExit(f"no PDF at {pdf}")
    return fitz.open(pdf)


def dump_text(pdf: Path, destination: Path) -> int:
    """Write the paper's text, one delimited block per page. Returns page count."""
    document = _open(pdf)
    blocks = []
    for number, page in enumerate(document, start=1):
        blocks.append(f"\n=================== PAGE {number} of {len(document)} ===================\n")
        blocks.append(page.get_text())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(blocks), encoding="utf-8")
    return len(document)


def render_pages(pdf: Path, destination: Path, *, zoom: float = 2.0, pages: str = "") -> list[Path]:
    """Render pages to PNG. *pages* is a comma-separated list, empty meaning all."""
    import fitz

    document = _open(pdf)
    wanted = (
        {int(part) for part in pages.split(",") if part.strip()}
        if pages.strip()
        else set(range(1, len(document) + 1))
    )
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for number, page in enumerate(document, start=1):
        if number not in wanted:
            continue
        target = destination / f"p{number:02d}.png"
        page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(target)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pair_id", help="a pair id from bench/dev/pairs.csv")
    parser.add_argument("--pairs-root", type=Path, default=DEFAULT_PAIRS_ROOT)
    parser.add_argument("--out", type=Path, required=True, help="directory for the outputs")
    parser.add_argument("--zoom", type=float, default=2.0)
    parser.add_argument(
        "--pages",
        default="",
        help="comma-separated page numbers to render as images; omit to render none",
    )
    arguments = parser.parse_args(argv)

    pdf = arguments.pairs_root / arguments.pair_id / "paper" / "paper.pdf"
    text_path = arguments.out / f"{arguments.pair_id}.txt"
    pages = dump_text(pdf, text_path)
    print(f"{pages} pages of text -> {text_path}")
    print(LIGATURE_NOTE)

    if arguments.pages.strip():
        images = render_pages(
            pdf, arguments.out / arguments.pair_id, zoom=arguments.zoom, pages=arguments.pages
        )
        print(f"{len(images)} page image(s) -> {arguments.out / arguments.pair_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
