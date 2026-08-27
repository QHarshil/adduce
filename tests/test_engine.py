"""End-to-end pipeline: scan → collect → evaluate → suppress → score."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from adduce import engine
from adduce.engine import baseline_snapshot, regressions_against, run_check
from adduce.report.json_report import render as render_json
from adduce.report.markdown import render as render_markdown
from adduce.rules import discover_rules
from adduce.rules.base import Category, Finding, Rule, Status
from adduce.rules.registry import RulePluginWarning

WELL_FORMED = {
    "README.md": (
        "# Demo\n\n## Installation\n\n```bash\npip install -r requirements.txt\n```\n\n"
        "## Reproducing results\n\n```bash\nbash run.sh\npython train.py --config configs/main.yaml\n```\n\n"
        "## Expected results\n\n| Metric | Value |\n|---|---|\n| Acc | 92.1 |\n\n"
        "## Hardware\n\n1x NVIDIA A100, ~2 hours. Results from commit abc1234.\n\n"
        "## Data\n\nhttps://zenodo.org/record/1234567 (DOI: 10.5281/zenodo.1234567), see scripts/download_data.sh\n"
    ),
    "LICENSE": "MIT License\n",
    "CITATION.cff": "cff-version: 1.2.0\ntitle: demo\n",
    "requirements.txt": "torch==2.1.0\nnumpy==1.26.0\npyyaml==6.0.1\n",
    ".python-version": "3.11\n",
    "Dockerfile": "FROM python:3.11-slim\n",
    "run.sh": "#!/bin/bash\npython train.py\n",
    "SHA256SUMS": "abc data.tar\n",
    "scripts/download_data.sh": "curl -O https://example.org/d.tar\n",
    "configs/main.yaml": "lr: 0.001\n",
    "train.py": (
        "import argparse\nimport random\nimport yaml\nimport numpy as np\nimport torch\n"
        "from torch.utils.data import DataLoader\n\n"
        "def set_seed(seed):\n"
        "    random.seed(seed)\n"
        "    np.random.seed(seed)\n"
        "    torch.manual_seed(seed)\n"
        "    torch.cuda.manual_seed_all(seed)\n"
        "    torch.backends.cudnn.deterministic = True\n"
        "    torch.backends.cudnn.benchmark = False\n\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--config')\n"
        "    args = parser.parse_args()\n"
        "    with open(args.config) as f:\n"
        "        cfg = yaml.safe_load(f)\n"
        "    set_seed(0)\n"
        "    g = torch.Generator()\n"
        "    g.manual_seed(0)\n"
        "    loader = DataLoader(None, shuffle=True, generator=g, num_workers=2, worker_init_fn=id)\n\n"
        "if __name__ == '__main__':\n    main()\n"
    ),
}

BARE = {
    "model.py": "import torch\nnet = torch.nn.Linear(2, 2)\n",
    "requirements.txt": "torch\n",
}


def _write(root, files):
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_well_formed_repo_scores_high(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    assert result.card.total >= 75, result.card.to_dict()
    seed = next(f for f in result.card.findings if f.rule_id == "R-DET-001")
    assert seed.status is Status.PASS


def test_bare_repo_scores_low(tmp_path):
    _write(tmp_path, BARE)
    result = run_check(tmp_path)
    assert result.card.total <= 40, result.card.to_dict()


def test_scores_separate_good_from_bad(tmp_path):
    good_root = tmp_path / "good"
    bad_root = tmp_path / "bad"
    good_root.mkdir()
    bad_root.mkdir()
    _write(good_root, WELL_FORMED)
    _write(bad_root, BARE)
    assert run_check(good_root).card.total - run_check(bad_root).card.total >= 40


def test_inline_suppression_marks_finding(tmp_path):
    files = dict(BARE)
    files["model.py"] = (
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(None, shuffle=True)  # adduce: ignore=R-DET-004\n"
    )
    _write(tmp_path, files)
    result = run_check(tmp_path)
    finding = next(f for f in result.card.findings if f.rule_id == "R-DET-004")
    assert finding.suppressed


def test_config_ignore_suppresses(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = 'ignore = ["R-LIC-001"]\n'
    _write(tmp_path, files)
    result = run_check(tmp_path)
    finding = next(f for f in result.card.findings if f.rule_id == "R-LIC-001")
    assert finding.suppressed


def test_config_profile_and_cli_override(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = 'profile = "acm"\n'
    _write(tmp_path, files)
    assert run_check(tmp_path).card.profile_name == "acm"
    assert run_check(tmp_path, profile_name="strict").card.profile_name == "strict"


def test_reviewer_policy_does_not_apply_repository_scoring_configuration(tmp_path):
    files = dict(BARE)
    files["adduce.toml"] = (
        'profile = "acm"\n'
        'ignore = ["R-LIC-001"]\n'
        'exclude = ["third_party"]\n'
        "fail-under = 100\n"
    )
    files["third_party/vendor.py"] = "print('scanned')\n"
    _write(tmp_path, files)

    result = run_check(tmp_path, honor_repository_policy=False)
    finding = next(f for f in result.card.findings if f.rule_id == "R-LIC-001")

    assert result.config.source == "adduce.toml"
    assert result.config.repository_policy_honored is False
    assert result.card.profile_name == "default"
    assert result.config.ignore == frozenset()
    assert result.config.exclude == ()
    assert result.config.fail_under is None
    assert finding.suppressed is False
    assert result.repo.exists("third_party/vendor.py")


def test_exclude_directories(tmp_path):
    files = dict(WELL_FORMED)
    files["third_party/vendor.py"] = "from sklearn.cluster import KMeans\nKMeans()\n"
    _write(tmp_path, files)
    with_vendor = run_check(tmp_path)
    without_vendor = run_check(tmp_path, exclude=("third_party",))
    ids_with = {f.rule_id: f.status for f in with_vendor.card.findings}
    ids_without = {f.rule_id: f.status for f in without_vendor.card.findings}
    assert ids_with.get("R-DET-006") in (Status.FAIL, Status.PARTIAL)
    assert ids_without.get("R-DET-006", Status.NOT_APPLICABLE) is Status.NOT_APPLICABLE


def test_baseline_regression_detection(tmp_path):
    _write(tmp_path, WELL_FORMED)
    good = run_check(tmp_path)
    snapshot = baseline_snapshot(good.card)
    assert regressions_against(good.card, snapshot) == []

    # Degrade determinism: strip the seeding helper.
    (tmp_path / "train.py").write_text(
        "import torch\nfrom torch.utils.data import DataLoader\n"
        "loader = DataLoader(None, shuffle=True)\n",
        encoding="utf-8",
    )
    worse = run_check(tmp_path)
    regressed_ids = {f.rule_id for f in regressions_against(worse.card, snapshot)}
    assert "R-DET-001" in regressed_ids


def _card_with_nothing_assessed(root):
    """A real card whose every rule was skipped, so nothing reached a verdict."""
    return run_check(root, rules=[]).card


def test_a_baseline_of_a_card_with_no_score_records_no_score(tmp_path):
    _write(tmp_path, WELL_FORMED)
    card = _card_with_nothing_assessed(tmp_path)

    snapshot = baseline_snapshot(card)

    assert card.total is None
    assert snapshot["total"] is None
    assert snapshot["rules"] == {}
    assert regressions_against(card, snapshot) == []


def test_new_rules_are_not_regressions(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    empty_baseline = {"version": 1, "rules": {}}
    assert regressions_against(result.card, empty_baseline) == []


class _AlwaysRule(Rule):
    id = "R-TEST-001"
    category = Category.DOCUMENTATION
    title = "Applies"
    weight = 1

    def evaluate(self, ev):
        return self.finding(Status.PASS, 1.0, "detected")


class _NeverRule(_AlwaysRule):
    id = "R-TEST-002"
    title = "Does not apply"

    def applies_to(self, repo):
        return False


def test_rules_skipped_before_evaluation_are_counted_outside_both_denominators(tmp_path):
    _write(tmp_path, WELL_FORMED)

    card = run_check(tmp_path, rules=[_AlwaysRule(), _NeverRule(), _NeverRule()]).card

    assert card.skipped_inapplicable == 2
    assert [f.rule_id for f in card.findings] == ["R-TEST-001"]
    assert card.considered_rules == 1
    assert card.applicable_rules == 1
    assert card.coverage == 100.0


SECRET_IN_EXCEPTION = "aws_secret_access_key=wJalrXUtnFEMI"
#: The value alone. Sanitising punctuation leaves this intact, so the
#: no-echo assertions have to name it separately from the pair above.
SECRET_VALUE = "wJalrXUtnFEMI"


class _RaisingRule(_AlwaysRule):
    """Defined here, so ``__module__`` is the test module and not ``adduce.*``."""

    id = "R-TEST-003"
    title = "Raises"

    def evaluate(self, ev):
        raise RuntimeError(SECRET_IN_EXCEPTION)


class _MislabelledRule(_AlwaysRule):
    id = "R-TEST-004"
    title = "Reports under another id"

    def evaluate(self, ev):
        return Finding(
            rule_id="R-DET-001",
            category=Category.DETERMINISM,
            title="Impersonated",
            status=Status.PASS,
            confidence=1.0,
            message="detected",
            remediation="",
            weight=1,
        )


class _BuiltinRaisingRule(_RaisingRule):
    id = "R-TEST-005"


class _BuiltinMislabelledRule(_MislabelledRule):
    id = "R-TEST-006"


class _NoReturnRule(_AlwaysRule):
    id = "R-TEST-007"
    title = "Forgets to return"

    def evaluate(self, ev):
        return None


class _WrongTypeRule(_AlwaysRule):
    id = "R-TEST-008"
    title = "Returns the wrong type"

    def evaluate(self, ev):
        return {"rule_id": self.id, "status": "pass"}


class _SpoofedModuleRule(_RaisingRule):
    """A pack claiming to be a built-in. One assignment used to be enough."""

    __module__ = "adduce.rules.docs"
    id = "R-TEST-009"


#: A class name is arbitrary text when the class is built by ``type()``: this
#: one carries a secret, a Markdown heading, a table pipe and a length no
#: identifier has.
FORGED_NAME = (
    f"{SECRET_IN_EXCEPTION}\n\n## Injected heading\n"
    "| R-DET-001 | pass | 100% | all good |" + "A" * 4000
)


class _ForgedNameRule(_AlwaysRule):
    id = "R-TEST-010"
    title = "Raises a forged exception class"

    def evaluate(self, ev):
        raise type(FORGED_NAME, (Exception,), {})()


def _as_builtin(monkeypatch, *rule_classes):
    """Treat ``rule_classes`` as shipped built-ins for the duration of a test.

    The engine keys built-in-ness on class identity, so faking it means adding
    to that sequence -- which is exactly the thing a rule pack cannot do.
    """
    monkeypatch.setattr(
        engine,
        "_BUILTIN_RULE_CLASSES",
        engine._BUILTIN_RULE_CLASSES + tuple(rule_classes),
    )


def _degraded(card, rule_id):
    return [f for f in card.findings if f.rule_id == rule_id and f.status is Status.UNKNOWN]


def test_one_raising_third_party_rule_does_not_discard_the_audit(tmp_path):
    _write(tmp_path, WELL_FORMED)
    builtins = discover_rules(include_plugins=False)
    expected_ids = {f.rule_id for f in run_check(tmp_path, rules=builtins).card.findings}

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[*builtins, _RaisingRule()]).card

    returned_ids = {f.rule_id for f in card.findings}
    assert expected_ids <= returned_ids
    assert len(_degraded(card, "R-TEST-003")) == 1


def test_a_degraded_finding_carries_the_rule_identity_and_claims_nothing(tmp_path):
    _write(tmp_path, WELL_FORMED)
    rule = _RaisingRule()

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[_AlwaysRule(), rule]).card

    (finding,) = _degraded(card, "R-TEST-003")
    assert finding.category is rule.category
    assert finding.title == rule.title
    assert finding.weight == rule.weight
    assert finding.severity == rule.effective_severity
    assert finding.confidence == 0.0
    assert finding.locations == []
    assert "RuntimeError" in finding.message
    assert SECRET_IN_EXCEPTION not in finding.message + finding.remediation
    assert SECRET_VALUE not in finding.message + finding.remediation


def test_a_degraded_rule_is_applicable_but_not_assessed(tmp_path):
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning):
        result = run_check(tmp_path, rules=[_AlwaysRule(), _RaisingRule()])

    card = result.card
    counters = result.telemetry.snapshot()["counters"]
    assert card.considered_rules == 2
    assert card.applicable_rules == 2
    assert card.evaluated_rules == 1
    assert card.unknown_rules == 1
    assert card.coverage == 50.0
    # The counter means rule functions that returned a finding; the engine
    # synthesised this one, so it belongs to neither that count nor coverage.
    assert counters["rules.evaluated"] == 1
    assert counters["rules.degraded"] == 1
    # test_telemetry asserts rules.evaluated == len(findings) over built-ins
    # only, where nothing degrades. Degradation splits that identity in two.
    assert counters["rules.evaluated"] != len(card.findings)
    assert counters["rules.evaluated"] + counters["rules.degraded"] == len(card.findings)


def test_the_degradation_warning_names_the_rule_and_the_exception_type_only(tmp_path):
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning) as caught:
        run_check(tmp_path, rules=[_RaisingRule()])

    (warning,) = [w for w in caught if w.category is RulePluginWarning]
    text = str(warning.message)
    assert "R-TEST-003" in text
    assert "RuntimeError" in text
    assert SECRET_IN_EXCEPTION not in text
    assert SECRET_VALUE not in text
    assert "Traceback" not in text
    assert "engine.py" not in text


def test_a_raising_builtin_rule_propagates(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _as_builtin(monkeypatch, _BuiltinRaisingRule)

    with pytest.raises(RuntimeError):
        run_check(tmp_path, rules=[_BuiltinRaisingRule()])


def test_every_shipped_rule_class_is_recognised_as_builtin():
    assert all(not engine._is_third_party(rule) for rule in discover_rules(include_plugins=False))


def test_claiming_an_adduce_module_does_not_buy_builtin_treatment(tmp_path):
    """The spoof this test file uses elsewhere must not work from a rule pack."""
    _write(tmp_path, WELL_FORMED)
    assert _SpoofedModuleRule.__module__.startswith("adduce.")

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[_SpoofedModuleRule()]).card

    assert len(_degraded(card, "R-TEST-009")) == 1


def test_a_subclass_of_a_builtin_rule_is_contained(tmp_path):
    subclass = type("_SubclassedBuiltin", (type(discover_rules(include_plugins=False)[0]),), {
        "id": "R-TEST-011",
        "evaluate": lambda self, ev: (_ for _ in ()).throw(RuntimeError("boom")),
    })
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[subclass()]).card

    assert len(_degraded(card, "R-TEST-011")) == 1


def test_a_third_party_rule_reporting_under_another_id_is_degraded(tmp_path):
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[_AlwaysRule(), _MislabelledRule()]).card

    assert [f.rule_id for f in card.findings] == ["R-TEST-001", "R-TEST-004"]
    assert len(_degraded(card, "R-TEST-004")) == 1
    assert not any(f.rule_id == "R-DET-001" for f in card.findings)


def test_a_builtin_rule_reporting_under_another_id_propagates(tmp_path, monkeypatch):
    _write(tmp_path, WELL_FORMED)
    _as_builtin(monkeypatch, _BuiltinMislabelledRule)

    with pytest.raises(ValueError, match="R-TEST-006"):
        run_check(tmp_path, rules=[_BuiltinMislabelledRule()])


@pytest.mark.parametrize(
    ("rule_class", "rule_id"),
    [(_NoReturnRule, "R-TEST-007"), (_WrongTypeRule, "R-TEST-008")],
)
def test_a_third_party_rule_returning_no_finding_is_degraded(tmp_path, rule_class, rule_id):
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[_AlwaysRule(), rule_class()]).card

    (finding,) = _degraded(card, rule_id)
    assert "not a finding" in finding.message


@pytest.mark.parametrize("rule_class", [_NoReturnRule, _WrongTypeRule])
def test_a_builtin_rule_returning_no_finding_propagates(tmp_path, monkeypatch, rule_class):
    _write(tmp_path, WELL_FORMED)
    _as_builtin(monkeypatch, rule_class)

    with pytest.raises(ValueError, match="unusable finding"):
        run_check(tmp_path, rules=[rule_class()])


def test_a_forged_exception_class_name_reaches_neither_warning_nor_report(tmp_path):
    """A class name is only an identifier when it was declared as one."""
    _write(tmp_path, WELL_FORMED)

    with pytest.warns(RulePluginWarning) as caught:
        result = run_check(tmp_path, rules=[*discover_rules(include_plugins=False), _ForgedNameRule()])

    (warning,) = [w for w in caught if w.category is RulePluginWarning]
    (finding,) = _degraded(result.card, "R-TEST-010")
    rendered = [str(warning.message), finding.message, finding.remediation]
    rendered += [render_markdown(result), render_json(result)]
    for text in rendered:
        assert SECRET_IN_EXCEPTION not in text
        assert SECRET_VALUE not in text
        assert "## Injected heading" not in text
        assert "A" * 100 not in text
    # The payload's newlines are what forge a heading and break a table row,
    # so the quoted text itself must be single-line.
    assert "\n" not in finding.message
    assert "\n" not in str(warning.message)
    assert "no usable class name" in finding.message
    assert len(str(warning.message)) < 200
    assert len(finding.message) < 200
    # The table row the payload aimed to forge is not in the rendered body.
    assert "| R-DET-001 | pass | 100% | all good |" not in render_markdown(result)


def test_a_degraded_finding_is_still_suppressible_by_configuration(tmp_path):
    files = dict(WELL_FORMED)
    files["adduce.toml"] = 'ignore = ["R-TEST-003"]\n'
    _write(tmp_path, files)

    with pytest.warns(RulePluginWarning):
        card = run_check(tmp_path, rules=[_RaisingRule()]).card

    (finding,) = _degraded(card, "R-TEST-003")
    assert finding.suppressed


def test_git_metadata_collected(tmp_path):
    _write(tmp_path, WELL_FORMED)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "tag", "v1.0"], cwd=tmp_path, check=True)
    result = run_check(tmp_path)
    vcs = next(f for f in result.card.findings if f.rule_id == "R-VER-002")
    assert vcs.status is Status.PASS
    assert result.repo.git.head_commit


@pytest.mark.skipif(sys.platform == "win32", reason="executable Git fixture uses a POSIX shebang")
def test_git_metadata_never_executes_repository_fsmonitor(tmp_path):
    _write(tmp_path, WELL_FORMED)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    marker = tmp_path.parent / "fsmonitor-executed"
    helper = tmp_path.parent / "fsmonitor-helper.py"
    helper.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.fsmonitor", str(helper)],
        cwd=tmp_path,
        check=True,
    )

    result = run_check(tmp_path)

    assert result.repo.git.is_repo
    assert result.repo.git.tracked_files
    assert not marker.exists()


def test_json_serialisation_round_trip(tmp_path):
    _write(tmp_path, WELL_FORMED)
    result = run_check(tmp_path)
    payload = json.loads(json.dumps(result.card.to_dict()))
    assert payload["total"] == round(result.card.total, 1)
    assert {c["category"] for c in payload["categories"]}


class _HashRaisingMeta(type):
    """A metaclass whose classes cannot be hashed."""

    def __hash__(cls):
        raise RuntimeError("this class refuses to be hashed")


class _UnhashableRule(Rule, metaclass=_HashRaisingMeta):
    id = "R-TEST-010"
    category = Category.DETERMINISM
    title = "Unhashable rule class"
    weight = 1

    def evaluate(self, ev):
        raise RuntimeError("boom")


def test_a_rule_whose_class_cannot_be_hashed_is_still_contained(tmp_path):
    """Deciding built-in-ness must not ask the class a question it can answer.

    Set membership consults ``__hash__``, so a pack supplying a metaclass whose
    ``__hash__`` raises would abort the audit from inside the test that exists
    to contain it.
    """
    (tmp_path / "train.py").write_text("import torch\n")
    with pytest.warns(RulePluginWarning):
        result = run_check(tmp_path, rules=[_UnhashableRule()])
    assert _degraded(result.card, "R-TEST-010")


def _equal_to_everything_rule():
    builtin = engine._BUILTIN_RULE_CLASSES[0]

    class _EqualsAnythingMeta(type):
        def __eq__(cls, other):
            return True

        def __hash__(cls):
            return hash(builtin)

    class _ImpersonatingRule(Rule, metaclass=_EqualsAnythingMeta):
        id = "R-TEST-011"
        category = Category.DETERMINISM
        title = "Rule impersonating a built-in"
        weight = 1

        def evaluate(self, ev):
            raise RuntimeError("boom")

    return _ImpersonatingRule()


def test_a_rule_class_claiming_equality_with_a_builtin_is_still_contained(tmp_path):
    """A pack must not talk its way into the built-in branch.

    ``__eq__`` returning True with a colliding ``__hash__`` reads as a built-in
    under set membership, which would hand back the power to abort the audit.
    """
    (tmp_path / "train.py").write_text("import torch\n")
    rule = _equal_to_everything_rule()
    with pytest.warns(RulePluginWarning):
        result = run_check(tmp_path, rules=[rule])
    assert _degraded(result.card, "R-TEST-011")


class _ClassSpoofedResult:
    """An object that reports ``Finding`` as its class without being one."""

    @property
    def __class__(self):
        return Finding


class _ClassSpoofingRule(Rule):
    id = "R-TEST-012"
    category = Category.DETERMINISM
    title = "Rule returning a class-spoofed result"
    weight = 1

    def evaluate(self, ev):
        return _ClassSpoofedResult()


def test_a_class_spoofed_result_is_contained_rather_than_trusted(tmp_path):
    """``isinstance`` consults ``__class__``, so passing it proves nothing.

    The identity read has to sit inside the boundary: an object can satisfy the
    type test and then fail on the very next attribute.
    """
    (tmp_path / "train.py").write_text("import torch\n")
    with pytest.warns(RulePluginWarning):
        result = run_check(tmp_path, rules=[_ClassSpoofingRule()])
    assert _degraded(result.card, "R-TEST-012")
