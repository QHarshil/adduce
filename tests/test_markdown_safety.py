"""Untrusted repository text cannot create Markdown structure."""

from __future__ import annotations

from adduce.markdown_safety import (
    markdown_code_span,
    markdown_indented_lines,
    markdown_inline,
)


def test_inline_text_collapses_structure_and_escapes_html_and_tables():
    rendered = markdown_inline("claim\n## forged | <script> `code`")

    assert "\n" not in rendered
    assert "## forged" in rendered
    assert "\\|" in rendered
    assert "\\<script\\>" in rendered
    assert "\\`code\\`" in rendered


def test_code_span_chooses_a_non_colliding_fence():
    rendered = markdown_code_span("python -c 'print(`value`)'")

    assert rendered.startswith("``")
    assert rendered.endswith("``")
    assert "`value`" in rendered


def test_multiline_text_is_always_indented():
    lines = markdown_indented_lines("safe\n## forged\n<script>")

    assert lines == ["    safe", "    ## forged", "    <script>"]
