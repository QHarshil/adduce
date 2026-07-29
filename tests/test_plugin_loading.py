"""Failure isolation and deterministic ordering for third-party plugins."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from types import SimpleNamespace

from adduce.report import ReporterPluginWarning, _discover_renderers
from adduce.rules import registry
from adduce.rules.base import Rule


@dataclass
class FakeEntryPoint:
    name: str
    module: str
    value: str
    target: object | None = None
    broken: bool = False

    @property
    def dist(self) -> SimpleNamespace:
        return SimpleNamespace(name=f"distribution-{self.name}")

    def load(self) -> object:
        if self.broken:
            raise RuntimeError("private-token\n[red] arbitrary plugin failure")
        return self.target


class FirstDuplicateRule(Rule):
    id = "X-PLUGIN-001"


class SecondDuplicateRule(Rule):
    id = "X-PLUGIN-001"


class UniquePluginRule(Rule):
    id = "X-PLUGIN-002"


class ExplodingPluginRule(Rule):
    id = "X-PLUGIN-003"

    def __init__(self) -> None:
        raise RuntimeError("broken constructor")


class EmptyIdPluginRule(Rule):
    id = ""


def _module(*rules: object) -> SimpleNamespace:
    return SimpleNamespace(RULES=rules)


def test_rule_plugins_are_isolated_and_deterministic(monkeypatch) -> None:
    builtin_id = registry.BUILTIN_RULES[0].id

    class BuiltinShadowRule(Rule):
        id = builtin_id

    entries = [
        FakeEntryPoint(
            "z-last",
            "example.second",
            "example.second:plugin",
            _module(SecondDuplicateRule),
        ),
        FakeEntryPoint("broken-load", "example.broken", "example.broken:plugin", broken=True),
        FakeEntryPoint(
            "invalid-rules",
            "example.invalid",
            "example.invalid:plugin",
            SimpleNamespace(RULES=None),
        ),
        FakeEntryPoint(
            "a-first",
            "example.first",
            "example.first:plugin",
            _module(
                object,
                ExplodingPluginRule,
                EmptyIdPluginRule,
                BuiltinShadowRule,
                FirstDuplicateRule,
                UniquePluginRule,
            ),
        ),
        FakeEntryPoint(
            "builtin",
            "adduce.rules.builtin",
            "adduce.rules.builtin",
            broken=True,
        ),
    ]
    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: reversed(entries))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = registry.discover_rules()
    by_id = {rule.id: rule for rule in rules}

    assert isinstance(by_id[builtin_id], registry.BUILTIN_RULES[0])
    assert isinstance(by_id[FirstDuplicateRule.id], FirstDuplicateRule)
    assert isinstance(by_id[UniquePluginRule.id], UniquePluginRule)
    assert ExplodingPluginRule.id not in by_id
    assert [rule.id for rule in rules].count(FirstDuplicateRule.id) == 1

    diagnostics = [
        str(item.message)
        for item in caught
        if issubclass(item.category, registry.RulePluginWarning)
    ]
    assert diagnostics == [
        "Skipped adduce.rules plugin a-first (example.first:plugin): "
        "RULES contains a non-Rule class.",
        "Skipped adduce.rules plugin broken-load (example.broken:plugin): "
        "entry-point loading failed.",
        "Skipped adduce.rules plugin invalid-rules (example.invalid:plugin): "
        "RULES is missing or is not iterable.",
        "Skipped adduce.rules plugin a-first (example.first:plugin): Rule construction failed.",
        "Skipped adduce.rules plugin a-first (example.first:plugin): Rule id is invalid.",
        "Skipped adduce.rules plugin a-first (example.first:plugin): "
        f"Rule id {builtin_id} conflicts with an existing rule.",
        "Skipped adduce.rules plugin z-last (example.second:plugin): "
        f"Rule id {FirstDuplicateRule.id} conflicts with an existing rule.",
    ]
    assert all("private-token" not in message for message in diagnostics)
    assert all("\n" not in message and "\r" not in message for message in diagnostics)


def test_rule_entry_point_discovery_failure_falls_back_to_builtins(monkeypatch) -> None:
    def fail_discovery(**_kwargs):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(registry, "entry_points", fail_discovery)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = registry.discover_rules()

    assert [type(rule) for rule in rules] == list(registry.BUILTIN_RULES)
    assert len(caught) == 1
    assert caught[0].category is registry.RulePluginWarning
    assert str(caught[0].message) == (
        "Could not discover adduce.rules plugins; built-in rules remain available."
    )


def test_disabling_rule_plugins_does_not_query_entry_points(monkeypatch) -> None:
    def unexpected_discovery(**_kwargs):
        raise AssertionError("plugin discovery should be disabled")

    monkeypatch.setattr(registry, "entry_points", unexpected_discovery)

    rules = registry.discover_rules(include_plugins=False)

    assert [type(rule) for rule in rules] == list(registry.BUILTIN_RULES)


def test_rule_plugin_diagnostic_sanitizes_entry_point_metadata(monkeypatch) -> None:
    entry = FakeEntryPoint(
        "bad\nname",
        "example.invalid",
        "example.invalid:plugin\r[red]",
        _module(UniquePluginRule),
    )
    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [entry])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = registry.discover_rules()

    assert UniquePluginRule.id not in {rule.id for rule in rules}
    assert len(caught) == 1
    message = str(caught[0].message)
    assert caught[0].category is registry.RulePluginWarning
    assert message == (
        "Skipped adduce.rules plugin bad?name (example.invalid:plugin?red?): "
        "entry-point name is invalid."
    )
    assert "\n" not in message and "\r" not in message


def test_rule_plugin_iteration_failure_does_not_partially_register_rules(
    monkeypatch,
) -> None:
    class FailingRules:
        def __iter__(self):
            yield UniquePluginRule
            raise RuntimeError("iteration failed")

    entry = FakeEntryPoint(
        "partial",
        "example.partial",
        "example.partial:plugin",
        SimpleNamespace(RULES=FailingRules()),
    )
    monkeypatch.setattr(registry, "entry_points", lambda **_kwargs: [entry])

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rules = registry.discover_rules()

    assert UniquePluginRule.id not in {rule.id for rule in rules}
    assert [str(item.message) for item in caught] == [
        "Skipped adduce.rules plugin partial (example.partial:plugin): "
        "RULES iteration failed."
    ]


def test_reporter_plugins_are_isolated_non_shadowing_and_deterministic() -> None:
    def first_renderer(_result) -> str:
        return "first"

    def second_renderer(_result) -> str:
        return "second"

    entries = [
        FakeEntryPoint(
            "custom", "example.second", "example.second:render", second_renderer
        ),
        FakeEntryPoint("broken", "example.broken", "example.broken:render", broken=True),
        FakeEntryPoint("ignored", "example.invalid", "example.invalid:value", object()),
        FakeEntryPoint("json", "example.shadow", "example.shadow:render", first_renderer),
        FakeEntryPoint("custom", "example.first", "example.first:render", first_renderer),
    ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        renderers = _discover_renderers(reversed(entries))

    assert renderers["custom"] is first_renderer
    assert renderers["json"] is not first_renderer
    assert "broken" not in renderers
    assert "ignored" not in renderers

    diagnostics = [
        str(item.message)
        for item in caught
        if issubclass(item.category, ReporterPluginWarning)
    ]
    assert diagnostics == [
        "Skipped adduce.reporters plugin broken (example.broken:render): "
        "entry-point loading failed.",
        "Skipped adduce.reporters plugin custom (example.second:render): "
        "format name conflicts with an existing reporter.",
        "Skipped adduce.reporters plugin ignored (example.invalid:value): "
        "loaded object is not callable.",
        "Skipped adduce.reporters plugin json (example.shadow:render): "
        "format name conflicts with an existing reporter.",
    ]
    assert all("private-token" not in message for message in diagnostics)
    assert all("\n" not in message and "\r" not in message for message in diagnostics)


def test_reporter_plugin_diagnostic_sanitizes_entry_point_metadata() -> None:
    entry = FakeEntryPoint(
        "bad\nname",
        "example.invalid",
        "example.invalid:render\r[red]",
        lambda _result: "unused",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        renderers = _discover_renderers([entry])

    assert "bad\nname" not in renderers
    assert len(caught) == 1
    message = str(caught[0].message)
    assert caught[0].category is ReporterPluginWarning
    assert message == (
        "Skipped adduce.reporters plugin bad?name (example.invalid:render?red?): "
        "entry-point name is invalid."
    )
    assert "\n" not in message and "\r" not in message
