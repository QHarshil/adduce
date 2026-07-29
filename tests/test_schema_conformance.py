"""Offline conformance checks against versioned, authoritative schemas."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft7Validator, FormatChecker

from adduce.engine import run_check
from adduce.fixers import scaffold_citation
from adduce.report import RENDERERS
from tests.test_engine import BARE, _write

_SCHEMA_DIR = Path(__file__).with_name("schemas")
_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_HASHES = {
    "sarif-schema-2.1.0.json": "ad6db49878699b091f3eeb765b6e29e92a34bad4da88664d000c923b549c3a25",
    "cff-schema-1.2.0.json": "0b8d22140da702d766df318dcff3a91af2f39521298dcf36d76315fd99cc169b",
}
_FORMAT_CHECKER = FormatChecker()


def _load_schema(name: str) -> dict[str, Any]:
    path = _SCHEMA_DIR / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _SCHEMA_HASHES[name]
    schema = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    Draft7Validator.check_schema(schema)
    return schema


def test_sarif_renderer_conforms_to_oasis_2_1_0_schema(tmp_path: Path) -> None:
    _write(tmp_path, BARE)
    report = json.loads(RENDERERS["sarif"](run_check(tmp_path)))

    assert report["$schema"] == (
        "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/"
        "sarif-schema-2.1.0.json"
    )
    validator = Draft7Validator(
        _load_schema("sarif-schema-2.1.0.json"),
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(report)


def test_scaffolded_citation_conforms_to_cff_1_2_0_schema(tmp_path: Path) -> None:
    _write(tmp_path, BARE)
    generated = scaffold_citation(run_check(tmp_path))
    document = yaml.safe_load(generated.path.read_text(encoding="utf-8"))

    assert isinstance(document, dict)
    assert document["title"].startswith("[AUTHOR REVIEW REQUIRED]")
    assert "date-released" not in document
    assert "version" not in document
    validator = Draft7Validator(
        _load_schema("cff-schema-1.2.0.json"),
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(document)


def test_project_citation_conforms_to_cff_1_2_0_schema() -> None:
    document = yaml.safe_load((_ROOT / "CITATION.cff").read_text(encoding="utf-8"))

    assert isinstance(document, dict)
    validator = Draft7Validator(
        _load_schema("cff-schema-1.2.0.json"),
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(document)
