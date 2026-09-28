"""The pull request claim gate: who it closes, who it spares, and its workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import pr_claim_gate as gate

ROOT = Path(__file__).resolve().parents[1]
REPO = "QHarshil/adduce"


class FakeApi:
    """Records every call; serves issues from a dict and 404s the rest."""

    def __init__(self, issues: dict[int, dict[str, Any]] | None = None, fail: bool = False):
        self.issues = issues or {}
        self.fail = fail
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, payload: Any) -> Any:
        self.calls.append((method, path, payload))
        if self.fail:
            raise RuntimeError("api down")
        if method == "GET":
            number = int(path.rsplit("/", 1)[1])
            if number not in self.issues:
                raise gate.NotFoundError(path)
            return self.issues[number]
        return {}

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] != "GET"]


def _event(
    body: str = "",
    *,
    title: str = "Fix a thing",
    login: str = "outsider",
    association: str = "CONTRIBUTOR",
    user_type: str = "User",
    action: str = "opened",
    sender: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "sender": {"login": sender or login},
        "pull_request": {
            "number": 90,
            "title": title,
            "body": body,
            "author_association": association,
            "user": {"login": login, "type": user_type},
        },
    }


def _issue(*assignees: str) -> dict[str, Any]:
    return {"assignees": [{"login": name} for name in assignees]}


def _closed(api: FakeApi) -> bool:
    return (
        ("PATCH", f"/repos/{REPO}/pulls/90", {"state": "closed"}) in api.writes
        and any(path.endswith("/issues/90/comments") for _, path, _ in api.writes)
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fixes #69", [69]),
        ("(#69) and #70.", [69, 70]),
        ("#69 twice: #69", [69]),
        ("abc#1 a/#2 &#39; nothing", []),
        (f"https://github.com/{REPO}/issues/64", [64]),
        ("https://GitHub.com/qharshil/ADDUCE/issues/64", [64]),
        ("https://github.com/other/repo/issues/64", []),
        (f"https://github.com/{REPO}/pull/64", []),
    ],
)
def test_referenced_issue_numbers(text: str, expected: list[int]) -> None:
    assert gate.referenced_issue_numbers(text, REPO) == expected


def test_references_are_capped() -> None:
    text = " ".join(f"#{n}" for n in range(1, 50))
    assert gate.referenced_issue_numbers(text, REPO) == list(range(1, gate.MAX_REFERENCES + 1))


@pytest.mark.parametrize("association", sorted(gate.EXEMPT_ASSOCIATIONS))
def test_owner_members_and_collaborators_are_never_touched(association: str) -> None:
    api = FakeApi()
    decision = gate.run(_event(association=association), REPO, api)
    assert decision.action == "skip"
    assert api.calls == []


def test_bots_are_never_touched() -> None:
    api = FakeApi()
    decision = gate.run(_event(login="dependabot[bot]", user_type="Bot"), REPO, api)
    assert decision.action == "skip"
    assert api.calls == []


def test_a_maintainer_reopening_an_outside_pull_request_keeps_it_open() -> None:
    api = FakeApi()
    decision = gate.run(_event(action="reopened", sender="QHarshil"), REPO, api)
    assert decision.action == "skip"
    assert api.calls == []


def test_the_author_reopening_is_checked_again() -> None:
    api = FakeApi()
    decision = gate.run(_event(action="reopened"), REPO, api)
    assert decision.action == "close"
    assert _closed(api)


def test_no_reference_closes_with_one_comment() -> None:
    api = FakeApi()
    decision = gate.run(_event("A drive-by fix."), REPO, api)
    assert decision.action == "close"
    assert _closed(api)
    comments = [payload for _, path, payload in api.writes if path.endswith("/comments")]
    assert comments == [{"body": gate.CLOSE_COMMENT}]
    assert "discouraged" in gate.CLOSE_COMMENT


def test_an_unassigned_issue_does_not_count() -> None:
    api = FakeApi({69: _issue()})
    assert gate.run(_event("Fixes #69"), REPO, api).action == "close"
    assert _closed(api)


def test_an_issue_assigned_to_someone_else_does_not_count() -> None:
    api = FakeApi({64: _issue("hugosmoreira")})
    assert gate.run(_event("Fixes #64"), REPO, api).action == "close"
    assert _closed(api)


def test_an_issue_assigned_to_the_author_keeps_it_open() -> None:
    api = FakeApi({64: _issue("HugoSMoreira")})
    decision = gate.run(_event("Fixes #64", login="hugosmoreira"), REPO, api)
    assert decision.action == "keep"
    assert api.writes == []


def test_one_assigned_reference_among_several_is_enough() -> None:
    api = FakeApi({69: _issue(), 70: _issue("outsider")})
    assert gate.run(_event("See #69, fixes #70"), REPO, api).action == "keep"
    assert api.writes == []


def test_a_reference_in_the_title_counts() -> None:
    api = FakeApi({70: _issue("outsider")})
    assert gate.run(_event("", title="Fix truncation (#70)"), REPO, api).action == "keep"


def test_a_referenced_pull_request_does_not_count() -> None:
    api = FakeApi({12: {**_issue("outsider"), "pull_request": {"url": "x"}}})
    assert gate.run(_event("Follows #12"), REPO, api).action == "close"


def test_a_missing_issue_does_not_count() -> None:
    api = FakeApi()
    assert gate.run(_event("Fixes #999"), REPO, api).action == "close"


def test_an_api_failure_closes_nothing(tmp_path, monkeypatch, capsys) -> None:
    api = FakeApi(fail=True)
    with pytest.raises(RuntimeError):
        gate.run(_event("Fixes #64"), REPO, api)
    assert api.writes == []

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event("Fixes #64")), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "unused")
    monkeypatch.setattr(gate, "github_api", lambda token: api)
    assert gate.main() == 1
    assert "could not decide" in capsys.readouterr().err
    assert api.writes == []


def _workflow() -> tuple[str, dict[str, Any]]:
    text = (ROOT / ".github" / "workflows" / "pr-claim-gate.yml").read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=yaml.BaseLoader)


def test_the_workflow_never_touches_pull_request_code() -> None:
    text, workflow = _workflow()
    assert workflow["on"] == {"pull_request_target": {"types": ["opened", "reopened"]}}
    assert workflow["permissions"] == {
        "contents": "read",
        "issues": "read",
        "pull-requests": "write",
    }
    steps = workflow["jobs"]["gate"]["steps"]
    checkout = steps[0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"persist-credentials": "false"}
    assert all("ref" not in (step.get("with") or {}) for step in steps)
    for expression in ("pull_request.head", "head_ref", "head.sha", "head.ref"):
        assert expression not in text
    assert steps[1]["run"] == "python3 scripts/pr_claim_gate.py"


def test_the_workflow_pins_checkout_to_the_same_commit_as_ci() -> None:
    _, workflow = _workflow()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pinned = workflow["jobs"]["gate"]["steps"][0]["uses"]
    assert pinned in ci
