"""Hierarchical findings: the ``FindingItem`` child model and its guards."""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle

import pytest

import adduce.rules as rules_package
from adduce.profiles import load_profile
from adduce.rules.base import (
    Category,
    Finding,
    FindingItem,
    Location,
    Rule,
    Status,
    summarize_items,
)
from adduce.scoring import score


class _Rule(Rule):
    id = "demo.rule"
    category = Category.DOCUMENTATION
    title = "Demo"
    weight = 4


def _parent(status: Status = Status.PASS, items: tuple[FindingItem, ...] = ()) -> Finding:
    return Finding(
        rule_id="demo.rule",
        category=Category.DOCUMENTATION,
        title="Demo",
        status=status,
        confidence=0.9,
        message="",
        remediation="",
        weight=4,
        items=items,
    )


def _item(item_id: str, status: Status = Status.PASS, **kwargs) -> FindingItem:
    return FindingItem(id=item_id, status=status, message="observed", **kwargs)


def test_finding_without_items_is_unchanged_and_serialises_an_empty_list():
    finding = _parent()
    assert finding.items == ()
    assert finding.to_dict()["items"] == []


def test_rule_finding_keeps_working_with_positional_arguments():
    finding = _Rule().finding(
        Status.PARTIAL,
        0.7,
        "half detected",
        "do the thing",
        [Location("README.md", 3)],
    )
    assert finding.status is Status.PARTIAL
    assert finding.confidence == 0.7
    assert finding.message == "half detected"
    assert finding.remediation == "do the thing"
    assert finding.locations == [Location("README.md", 3)]
    assert finding.items == ()


def test_rule_finding_carries_items_as_a_tuple():
    items = [_item("citation:10.1234/a"), _item("citation:10.1234/b", Status.FAIL)]
    finding = _Rule().finding(Status.PARTIAL, 0.6, "one of two detected", items=items)
    assert isinstance(finding.items, tuple)
    assert [item.id for item in finding.items] == ["citation:10.1234/a", "citation:10.1234/b"]
    assert finding.to_dict()["items"][1]["status"] == "fail"


def test_duplicate_item_ids_are_rejected_and_the_error_names_the_id():
    with pytest.raises(ValueError, match="citation:10.1234/a"):
        _parent(items=(_item("citation:10.1234/a"), _item("citation:10.1234/a", Status.FAIL)))


def test_duplicate_item_ids_are_rejected_through_the_rule_helper():
    with pytest.raises(ValueError, match="duplicate finding item id 'dup'"):
        _Rule().finding(Status.PASS, 1.0, "", items=[_item("dup"), _item("dup")])


def test_non_string_id_is_rejected():
    with pytest.raises(ValueError, match="id is not a string, got int"):
        FindingItem(id=7, status=Status.PASS, message="")  # type: ignore[arg-type]


def test_empty_id_is_rejected():
    with pytest.raises(ValueError, match="id is empty"):
        FindingItem(id="", status=Status.PASS, message="")


def test_non_string_message_is_rejected():
    with pytest.raises(ValueError, match="'x' message is not a string, got int"):
        FindingItem(id="x", status=Status.PASS, message=3)  # type: ignore[arg-type]


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_the_unit_range_is_rejected(confidence):
    with pytest.raises(ValueError, match="'x' confidence is outside 0.0..1.0"):
        _item("x", confidence=confidence)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_confidence_is_rejected(confidence):
    with pytest.raises(ValueError, match="'x' confidence is not finite"):
        _item("x", confidence=confidence)


def test_non_numeric_confidence_is_rejected():
    with pytest.raises(ValueError, match="'x' confidence is not a number, got str"):
        _item("x", confidence="high")


def test_nested_dict_attribute_is_rejected():
    with pytest.raises(ValueError, match="attribute 'trail' holds an unrepresentable dict"):
        _item("x", attributes={"trail": {"nested": 1}})


def test_list_attribute_is_rejected():
    with pytest.raises(ValueError, match="attribute 'pages' holds an unrepresentable list"):
        _item("x", attributes={"pages": [1, 2]})


def test_bytes_attribute_is_rejected():
    with pytest.raises(ValueError, match="attribute 'blob' holds an unrepresentable bytes"):
        _item("x", attributes={"blob": b"\x00\x01"})


def test_non_string_attribute_key_is_rejected():
    with pytest.raises(ValueError, match="attribute key 7 is not a string"):
        _item("x", attributes={7: "a"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_attribute_value_is_rejected(value):
    with pytest.raises(ValueError, match="attribute 'delta' is not finite"):
        _item("x", attributes={"delta": value})


def test_scalar_attributes_are_accepted():
    item = _item(
        "x",
        attributes={"doi": "10.1234/a", "page": 4, "delta": 0.5, "quoted": True, "note": None},
    )
    assert item.to_dict()["attributes"]["quoted"] is True


def test_summarize_items_reports_every_status_including_zeros():
    counts = summarize_items([])
    assert set(counts) == set(Status)
    assert all(count == 0 for count in counts.values())
    assert counts[Status.NOT_APPLICABLE] == 0  # indexable without a presence check


def test_summarize_items_counts_a_mixed_collection():
    items = [
        _item("a", Status.PASS),
        _item("b", Status.PASS),
        _item("c", Status.FAIL),
        _item("d", Status.PARTIAL),
        _item("e", Status.UNKNOWN),
    ]
    assert summarize_items(items) == {
        Status.PASS: 2,
        Status.PARTIAL: 1,
        Status.FAIL: 1,
        Status.NOT_APPLICABLE: 0,
        Status.UNKNOWN: 1,
    }


def test_to_dict_round_trips_through_strict_json():
    finding = _parent(
        items=(
            _item(
                "citation:10.1234/a",
                Status.FAIL,
                confidence=0.25,
                locations=(Location("paper.tex", 12), Location("refs.bib")),
                remediation="add the reference",
                kind="citation",
                attributes={"doi": "10.1234/a", "page": 4, "delta": 0.5, "seen": False},
            ),
        )
    )
    payload = json.loads(json.dumps(finding.to_dict(), allow_nan=False))
    item = payload["items"][0]
    assert item["kind"] == "citation"
    assert item["locations"] == [
        {"path": "paper.tex", "line": 12},
        {"path": "refs.bib", "line": None},
    ]
    assert item["attributes"] == {"doi": "10.1234/a", "page": 4, "delta": 0.5, "seen": False}


def test_ten_thousand_items_construct_and_summarise():
    items = tuple(
        _item(f"assertion:{n}", Status.FAIL if n % 5 == 0 else Status.PASS)
        for n in range(10_000)
    )
    finding = _parent(items=items)
    assert len(finding.items) == 10_000
    assert summarize_items(finding.items) == {
        Status.PASS: 8_000,
        Status.PARTIAL: 0,
        Status.FAIL: 2_000,
        Status.NOT_APPLICABLE: 0,
        Status.UNKNOWN: 0,
    }


def test_a_generator_of_items_yields_every_item_in_to_dict():
    """A generator must not be consumed before it is materialised.

    Storing ``items`` exactly as passed once let the duplicate-id loop drain
    an iterator, leaving nothing behind for ``to_dict`` to serialise.
    """
    items = (_item(f"assertion:{n}") for n in range(50))
    finding = _parent(items=items)
    assert len(finding.items) == 50
    assert len(finding.to_dict()["items"]) == 50


def test_a_duplicate_id_from_a_generator_is_still_rejected():
    def source():
        yield _item("dup")
        yield _item("dup", Status.FAIL)

    with pytest.raises(ValueError, match="duplicate finding item id 'dup'"):
        _parent(items=source())


def test_mutating_a_source_list_after_construction_does_not_reach_the_finding():
    """A caller's list must be copied, not aliased, or a later append bypasses validation."""
    source = [_item("a"), _item("b")]
    finding = _parent(items=source)

    source.append(_item("c"))
    source.clear()

    assert [item.id for item in finding.items] == ["a", "b"]
    assert len(finding.to_dict()["items"]) == 2


def test_mutating_the_source_attributes_after_construction_does_not_reach_the_item():
    attrs = {"doi": "10.1234/a"}
    item = _item("x", attributes=attrs)

    attrs["doi"] = "10.1234/mutated"
    attrs["blob"] = b"\x00"  # never validated if it leaked through

    assert item.attributes == {"doi": "10.1234/a"}
    assert item.to_dict()["attributes"] == {"doi": "10.1234/a"}
    assert json.loads(json.dumps(item.to_dict())) == item.to_dict()


def test_the_aliasing_loop_yields_distinct_attributes_per_item():
    """The realistic failure: one dict hoisted out of a loop and reused."""
    attrs: dict = {}
    items = []
    for doi in ("10.1/a", "10.1/b", "10.1/c"):
        attrs["doi"] = doi
        items.append(_item(f"citation:{doi}", attributes=attrs))

    assert [item.attributes["doi"] for item in items] == ["10.1/a", "10.1/b", "10.1/c"]


def test_item_attributes_cannot_be_mutated_directly():
    item = _item("x", attributes={"doi": "10.1234/a"})
    with pytest.raises(TypeError):
        item.attributes["doi"] = "10.1234/mutated"  # type: ignore[index]


def test_a_list_of_locations_is_coerced_to_a_tuple_and_decoupled_from_its_source():
    source = [Location("paper.tex", 1)]
    item = _item("x", locations=source)  # type: ignore[arg-type]

    assert isinstance(item.locations, tuple)
    source.append(Location("refs.bib"))
    assert item.locations == (Location("paper.tex", 1),)


def test_deepcopy_preserves_every_field_and_stays_read_only():
    original = _item(
        "citation:10.1234/a",
        Status.FAIL,
        confidence=0.4,
        locations=(Location("paper.tex", 3), Location("refs.bib")),
        remediation="add the reference",
        kind="citation",
        attributes={"doi": "10.1234/a", "page": 4},
    )

    duplicate = copy.deepcopy(original)

    assert duplicate == original
    assert duplicate.id == original.id
    assert duplicate.status is original.status
    assert duplicate.message == original.message
    assert duplicate.attributes == original.attributes
    assert duplicate.locations == original.locations
    assert duplicate.attributes is not original.attributes
    with pytest.raises(TypeError):
        duplicate.attributes["doi"] = "mutated"  # type: ignore[index]


def test_deepcopy_is_decoupled_from_the_original_source_mapping():
    attrs = {"doi": "10.1234/a"}
    original = _item("x", attributes=attrs)
    duplicate = copy.deepcopy(original)

    attrs["doi"] = "10.1234/mutated"

    assert duplicate.attributes == {"doi": "10.1234/a"}
    assert original.attributes == {"doi": "10.1234/a"}


def test_pickle_round_trip_preserves_every_field_and_stays_read_only():
    original = _item(
        "citation:10.1234/a",
        Status.PARTIAL,
        locations=(Location("paper.tex", 3),),
        attributes={"doi": "10.1234/a"},
    )

    restored = pickle.loads(pickle.dumps(original))

    assert restored == original
    assert restored.status is original.status
    assert restored.attributes == original.attributes
    assert restored.locations == original.locations
    with pytest.raises(TypeError):
        restored.attributes["doi"] = "mutated"  # type: ignore[index]


def test_reduce_rebuilds_through_init_so_a_bad_field_revalidates_on_the_way_back():
    item = _item("x")
    cls, args = item.__reduce__()
    assert cls is FindingItem
    with pytest.raises(ValueError, match="id is empty"):
        cls(*(("",) + args[1:]))


def test_asdict_still_raises_type_error_to_dict_is_the_supported_serialisation_route():
    """Known limitation: ``asdict`` deep-copies fields directly and bypasses ``__reduce__``."""
    item = _item("x", attributes={"doi": "10.1234/a"})
    with pytest.raises(TypeError):
        dataclasses.asdict(item)
    assert item.to_dict()["attributes"] == {"doi": "10.1234/a"}


def test_item_status_never_moves_the_parent_status_score_or_weight():
    items = tuple(_item(f"a{n}", Status.FAIL) for n in range(6))
    with_items = _parent(Status.PASS, items=items)
    without = _parent(Status.PASS)

    assert with_items.status is Status.PASS
    assert with_items.weight == without.weight

    profile = load_profile("default")
    assert score([with_items], profile).total == score([without], profile).total == 100.0


def test_rules_package_all_pins_the_exact_covered_surface():
    """`docs/plugin-api.md` names every covered symbol as importable from here.

    Pinned explicitly so adding or removing a covered name is a deliberate,
    reviewed edit rather than a silent drop that leaves the docs wrong.
    """
    assert rules_package.__all__ == [
        "Category",
        "Finding",
        "FindingItem",
        "JsonValue",
        "Location",
        "Rule",
        "Status",
        "summarize_items",
        "BUILTIN_RULES",
        "discover_rules",
    ]
    for name in rules_package.__all__:
        assert hasattr(rules_package, name), name
