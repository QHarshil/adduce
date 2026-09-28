#!/usr/bin/env python3
"""Close an outside pull request that was opened without an assigned issue.

CONTRIBUTING.md asks contributors to be assigned an issue before they open a
pull request. `.github/workflows/pr-claim-gate.yml` runs this script on
`pull_request_target`. It reads the event payload and the GitHub API only. It
never checks out or runs code from the pull request, which is what makes the
privileged trigger safe to use.

A pull request is kept when its author is the owner, a member, a collaborator
or a bot, when someone other than its author reopened it, or when its title or
body references an issue in this repository that is assigned to its author.
Anything else gets one comment and is closed. If the API fails, nothing is
closed and the job fails, so a transient error cannot close a pull request
that should stay open.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

EXEMPT_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
MAX_REFERENCES = 20

# `#123` counts unless a word character, a slash or `&` precedes it, which
# rules out `abc#1`, a path fragment and an HTML character reference.
_SHORT_REF = re.compile(r"(?<![\w/&])#(\d+)\b")
_URL_REF = re.compile(
    r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)\b", re.IGNORECASE
)

CLOSE_COMMENT = (
    "Thanks for the pull request. Opening one without an assigned issue is "
    "discouraged. Comment on the issue first and wait for a maintainer to "
    'assign it to you (see "Claiming work and release gates" in '
    "CONTRIBUTING.md). This pull request does not reference an issue assigned "
    "to you, so it has been closed automatically. Once an issue is assigned to "
    "you, open a new pull request against `dev` that references it."
)


class NotFoundError(Exception):
    """The API returned 404 for the requested resource."""


Api = Callable[[str, str, "Mapping[str, Any] | None"], Any]


@dataclass(frozen=True)
class Decision:
    action: str  # "skip", "keep" or "close"
    reason: str


def referenced_issue_numbers(text: str, repository: str) -> list[int]:
    """Issue numbers referenced in *text*, in order, without duplicates."""
    found: list[tuple[int, int]] = []
    for match in _SHORT_REF.finditer(text):
        found.append((match.start(), int(match.group(1))))
    for match in _URL_REF.finditer(text):
        if match.group(1).casefold() == repository.casefold():
            found.append((match.start(), int(match.group(2))))
    numbers: list[int] = []
    for _, number in sorted(found):
        if number not in numbers:
            numbers.append(number)
    return numbers[:MAX_REFERENCES]


def exemption(event: Mapping[str, Any]) -> str | None:
    """Why the gate does not apply to this event, or None when it does."""
    pr = event["pull_request"]
    user = pr.get("user") or {}
    if user.get("type") == "Bot":
        return "author is a bot"
    association = str(pr.get("author_association", ""))
    if association in EXEMPT_ASSOCIATIONS:
        return f"author association is {association}"
    sender = (event.get("sender") or {}).get("login", "")
    author = user.get("login", "")
    # Only the author or someone with write access can reopen a pull request,
    # so a reopen by anyone else is a maintainer deciding to keep it.
    if event.get("action") == "reopened" and sender.casefold() != author.casefold():
        return f"reopened by {sender}"
    return None


def decide(
    pr: Mapping[str, Any], issues: Mapping[int, Mapping[str, Any] | None]
) -> Decision:
    """Keep the pull request if a referenced issue is assigned to its author."""
    author = str(pr["user"]["login"]).casefold()
    for number, issue in issues.items():
        if issue is None or "pull_request" in issue:
            continue
        assignees = {
            str(assignee.get("login", "")).casefold()
            for assignee in issue.get("assignees") or []
        }
        if author in assignees:
            return Decision("keep", f"references #{number}, assigned to the author")
    return Decision("close", "no referenced issue is assigned to the author")


def run(event: Mapping[str, Any], repository: str, api: Api) -> Decision:
    """Apply the gate to one pull request event, calling *api* for GitHub."""
    reason = exemption(event)
    if reason is not None:
        return Decision("skip", reason)
    pr = event["pull_request"]
    text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
    issues: dict[int, Mapping[str, Any] | None] = {}
    for number in referenced_issue_numbers(text, repository):
        try:
            issues[number] = api("GET", f"/repos/{repository}/issues/{number}", None)
        except NotFoundError:
            issues[number] = None
    decision = decide(pr, issues)
    if decision.action == "close":
        number = pr["number"]
        api(
            "POST",
            f"/repos/{repository}/issues/{number}/comments",
            {"body": CLOSE_COMMENT},
        )
        api("PATCH", f"/repos/{repository}/pulls/{number}", {"state": "closed"})
    return decision


def github_api(token: str, base: str = "https://api.github.com") -> Api:
    """A minimal GitHub REST client over urllib."""

    def call(method: str, path: str, payload: Mapping[str, Any] | None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFoundError(path) from exc
            raise
        return json.loads(body) if body else None

    return call


def main() -> int:
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            event = json.load(handle)
        repository = os.environ["GITHUB_REPOSITORY"]
        api = github_api(os.environ["GITHUB_TOKEN"])
        decision = run(event, repository, api)
    except Exception as exc:  # report and fail; never guess
        print(f"claim gate could not decide: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"{decision.action}: {decision.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
