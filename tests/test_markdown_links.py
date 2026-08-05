"""Behaviour of the local Markdown link checker."""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

import pytest
from scripts.check_markdown_links import MarkdownScanError, check_markdown_links, main

ROOT = Path(__file__).resolve().parents[1]
SDIST = ROOT / "dist" / "adduce-0.1.2.tar.gz"
BLOB_URL_RE = re.compile(r"https://github\.com/QHarshil/adduce/(?:blob|tree)/main/([^)#\s]+)")


def write(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def test_a_resolving_relative_link_passes(tmp_path):
    write(
        tmp_path,
        {
            "README.md": "See [the docs](docs/index.md) and [the policy](SECURITY.md).\n",
            "docs/index.md": "# Index\n\nBack to the [README](../README.md).\n",
            "SECURITY.md": "# Security\n",
        },
    )
    assert check_markdown_links(tmp_path) == []


def test_a_missing_target_names_the_source_file_and_line(tmp_path):
    write(tmp_path, {"README.md": "intro\n\nnext\n\n[gone](docs/gone.md)\n"})
    assert check_markdown_links(tmp_path) == [
        "README.md:5: link target does not exist: docs/gone.md"
    ]


def test_a_missing_cross_document_anchor_names_the_target_document(tmp_path):
    write(
        tmp_path,
        {
            "README.md": "[run it](docs/cli-reference.md#reproduce)\n",
            "docs/cli-reference.md": "# CLI reference\n",
        },
    )
    assert check_markdown_links(tmp_path) == [
        "README.md:1: anchor not found in docs/cli-reference.md: #reproduce"
    ]


def test_an_in_document_anchor_resolves_against_its_own_headings(tmp_path):
    write(tmp_path, {"README.md": "# What it reports\n\n[jump](#what-it-reports)\n[no](#absent)\n"})
    assert check_markdown_links(tmp_path) == [
        "README.md:4: anchor not found in README.md: #absent"
    ]


def test_heading_slugs_drop_punctuation_and_keep_hyphens(tmp_path):
    write(
        tmp_path,
        {
            "docs/index.md": (
                "# The `check` command: what it does!\n"
                "\n"
                "## Time-to-first-result\n"
                "\n"
                "[a](#the-check-command-what-it-does) [b](#time-to-first-result)\n"
            )
        },
    )
    assert check_markdown_links(tmp_path) == []


def test_repeated_headings_get_deterministic_occurrence_anchors(tmp_path):
    body = "# Usage\n\n## Notes\n\n# Usage\n\n# Usage\n\n"
    write(
        tmp_path / "resolving",
        {"docs/index.md": body + "[a](#usage) [b](#usage-1) [c](#usage-2) [d](#notes)\n"},
    )
    write(tmp_path / "beyond", {"docs/index.md": body + "[e](#usage-3)\n"})
    assert check_markdown_links(tmp_path / "resolving") == []
    assert check_markdown_links(tmp_path / "beyond") == [
        "docs/index.md:9: anchor not found in docs/index.md: #usage-3"
    ]


def test_explicit_html_anchors_are_honoured(tmp_path):
    write(
        tmp_path,
        {
            "docs/index.md": (
                '<a name="pinned"></a>\n'
                "\n"
                '# Title <span id="tagged"></span>\n'
                "\n"
                "[a](#pinned) [b](#tagged) [c](#title-span-idtaggedspan)\n"
            )
        },
    )
    assert check_markdown_links(tmp_path) == []


def test_links_inside_fenced_code_blocks_are_ignored(tmp_path):
    write(
        tmp_path,
        {
            "README.md": (
                "```markdown\n"
                "[gone](nowhere.md)\n"
                "```\n"
                "\n"
                "~~~\n"
                "[also gone](nowhere.md)\n"
                "~~~\n"
                "\n"
                "````\n"
                "```\n"
                "[still gone](nowhere.md)\n"
                "````\n"
                "\n"
                "An inline `[span](nowhere.md)` is code too.\n"
            )
        },
    )
    assert check_markdown_links(tmp_path) == []


def test_headings_inside_fenced_code_blocks_do_not_become_anchors(tmp_path):
    write(tmp_path, {"README.md": "```sh\n# install adduce\n```\n\n[c](#install-adduce)\n"})
    assert check_markdown_links(tmp_path) == [
        "README.md:5: anchor not found in README.md: #install-adduce"
    ]


def test_percent_encoded_and_angle_bracket_targets_resolve(tmp_path):
    write(
        tmp_path,
        {
            "README.md": "[a](docs/a%20b.md) [b](<docs/a b.md>) [c](docs/a%20c.md)\n",
            "docs/a b.md": "# A B\n",
        },
    )
    assert check_markdown_links(tmp_path) == [
        "README.md:1: link target does not exist: docs/a%20c.md"
    ]


def test_a_target_outside_the_root_is_rejected_even_when_it_exists(tmp_path):
    outside = write(tmp_path / "outside", {"etc/passwd.md": "# Secret\n"})
    root = write(tmp_path / "root", {"docs/index.md": "[escape](../../outside/etc/passwd.md)\n"})
    assert (outside / "etc" / "passwd.md").is_file()
    assert check_markdown_links(root) == [
        "docs/index.md:1: link target resolves outside the root: ../../outside/etc/passwd.md"
    ]


def test_images_are_ignored_unless_they_are_requested(tmp_path):
    write(tmp_path, {"README.md": "![logo](docs/logo.png)\n\n[![badge](docs/b.svg)](docs/i.md)\n"})
    assert check_markdown_links(tmp_path) == [
        "README.md:3: link target does not exist: docs/i.md"
    ]
    assert check_markdown_links(tmp_path, check_images=True) == [
        "README.md:1: link target does not exist: docs/logo.png",
        "README.md:3: link target does not exist: docs/i.md",
        "README.md:3: link target does not exist: docs/b.svg",
    ]


def test_reference_style_links_resolve_and_their_definitions_are_checked(tmp_path):
    write(
        tmp_path,
        {
            "README.md": (
                "See [the docs][docs], [Docs][] and [missing][nowhere].\n"
                "\n"
                "[docs]: docs/index.md\n"
                "[broken]: docs/gone.md\n"
            ),
            "docs/index.md": "# Index\n",
        },
    )
    assert check_markdown_links(tmp_path) == [
        "README.md:1: link reference is not defined: [nowhere]",
        "README.md:4: link target does not exist: docs/gone.md",
    ]


def test_a_directory_target_is_valid_only_when_the_directory_exists(tmp_path):
    write(
        tmp_path,
        {"README.md": "[here](docs) and [there](missing)\n", "docs/index.md": "# Index\n"},
    )
    assert check_markdown_links(tmp_path) == [
        "README.md:1: link target does not exist: missing"
    ]


def test_external_targets_are_never_resolved(tmp_path):
    write(
        tmp_path,
        {
            "README.md": (
                "[a](https://example.invalid/absent.md) "
                "[b](mailto:someone@example.com) "
                "[c](ftp://example.invalid/x) "
                "[d](https:///absent.md)\n"
            )
        },
    )
    assert check_markdown_links(tmp_path) == [
        "README.md:1: external link has no host: https:///absent.md"
    ]


def test_absent_documents_are_skipped_and_working_directories_are_not_scanned(tmp_path):
    write(
        tmp_path,
        {
            "corpus/README.md": "# Corpus\n",
            "corpus/clones/vendor/README.md": "[gone](nowhere.md)\n",
            "corpus/outputs/run-a/report.md": "[gone](nowhere.md)\n",
            "corpus/reports/summary.md": "[gone](nowhere.md)\n",
        },
    )
    assert not (tmp_path / "CONTRIBUTING.md").exists()
    assert check_markdown_links(tmp_path) == []


def test_main_reports_one_diagnostic_per_broken_link(tmp_path, capsys):
    write(tmp_path, {"README.md": "[a](x.md)\n[b](y.md)\n"})
    assert main(["--root", str(tmp_path)]) == 1
    reported = capsys.readouterr().err.splitlines()
    assert reported == [
        "README.md:1: link target does not exist: x.md",
        "README.md:2: link target does not exist: y.md",
        "2 broken markdown link(s)",
    ]


def test_main_honours_the_check_images_flag(tmp_path, capsys):
    write(tmp_path, {"README.md": "![logo](docs/logo.png)\n"})
    assert main(["--root", str(tmp_path)]) == 0
    assert "every local markdown link resolves" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "--check-images"]) == 1


def test_an_unusable_root_is_a_usage_error(tmp_path, capsys):
    absent = tmp_path / "absent"
    assert main(["--root", str(absent)]) == 2
    assert "root is not a directory" in capsys.readouterr().err
    with pytest.raises(MarkdownScanError):
        check_markdown_links(absent)


def test_the_repository_markdown_tree_has_no_broken_local_links():
    problems = check_markdown_links(ROOT)
    assert problems == [], "\n".join(problems)


def test_the_docs_index_links_back_to_the_repository_root_documents():
    index = ROOT / "docs" / "index.md"
    text = index.read_text(encoding="utf-8")
    for target in ("../CONTRIBUTING.md", "../SECURITY.md", "../corpus/README.md"):
        assert f"({target})" in text
        assert (index.parent / target).resolve().is_file()
    assert [p for p in check_markdown_links(ROOT) if p.startswith("docs/index.md:")] == []


def test_the_readme_links_into_the_documentation_tree_resolve():
    referenced = set(BLOB_URL_RE.findall((ROOT / "README.md").read_text(encoding="utf-8")))
    assert any(path.startswith("docs/") for path in referenced)
    missing = sorted(path for path in referenced if not (ROOT / path).exists())
    assert missing == []


@pytest.mark.skipif(not SDIST.is_file(), reason="no source distribution has been built")
def test_the_extracted_source_distribution_has_no_broken_local_links(tmp_path):
    with tarfile.open(SDIST) as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(tmp_path, filter="data")
        else:
            archive.extractall(tmp_path)
    extracted = tmp_path / SDIST.name.removesuffix(".tar.gz")
    assert (extracted / "CONTRIBUTING.md").is_file()
    assert (extracted / "docs" / "index.md").is_file()
    problems = check_markdown_links(extracted)
    assert problems == [], "\n".join(problems)
