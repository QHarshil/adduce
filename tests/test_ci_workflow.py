"""Invariants of the integration workflow.

These are properties that fail open: if one regresses, the workflow still runs
and still reports green, and nothing else in the suite notices. A required
check that stops covering a job, a job with no timeout, or an action that stops
being pinned are all invisible until they matter.

``yaml.BaseLoader`` is deliberate. Under the default loader YAML 1.1 turns the
``on:`` key into the boolean ``True``, and every scalar here is compared as the
string it is written as.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"

#: The aggregate check cannot require this one: it is dispatch- and
#: schedule-only, so on a pull request it skips, and a skipped job is not a
#: success.
DISPATCH_ONLY_JOB = "pypi-smoke"
AGGREGATE_JOB = "ci-ok"

_SHA = re.compile(r"@[0-9a-f]{40}$")


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        yield from job.get("steps", [])


def test_the_aggregate_check_covers_every_job_that_runs_on_a_pull_request():
    """A new job that is not added here is a job the required check does not
    cover, which is the failure this test exists to make loud."""
    workflow = _workflow(CI)
    jobs = set(workflow["jobs"])
    expected = jobs - {AGGREGATE_JOB, DISPATCH_ONLY_JOB}

    assert set(workflow["jobs"][AGGREGATE_JOB]["needs"]) == expected


def test_the_aggregate_check_does_not_require_the_dispatch_only_job():
    """Requiring it would block every merge, because it skips on pull requests."""
    needs = _workflow(CI)["jobs"][AGGREGATE_JOB]["needs"]
    assert DISPATCH_ONLY_JOB not in needs


def test_the_aggregate_check_runs_even_when_a_dependency_failed():
    """Without ``always()`` the job is skipped when a dependency fails, and a
    skipped required check reads as 'not failed' on branch protection."""
    assert _workflow(CI)["jobs"][AGGREGATE_JOB]["if"] == "always()"


def test_the_dispatch_only_job_stays_off_pull_requests():
    condition = _workflow(CI)["jobs"][DISPATCH_ONLY_JOB]["if"]
    assert "workflow_dispatch" in condition and "schedule" in condition
    assert "pull_request" not in condition


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_every_job_is_bounded_by_a_timeout(name):
    workflow = _workflow(WORKFLOWS / name)
    unbounded = [
        job for job, body in workflow["jobs"].items() if "timeout-minutes" not in body
    ]
    assert unbounded == []


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_no_checkout_leaves_a_credential_in_the_runner(name):
    """None of these jobs pushes, so a persisted credential only widens what a
    compromised step can reach."""
    leaking = [
        step
        for step in _steps(_workflow(WORKFLOWS / name))
        if "actions/checkout" in step.get("uses", "")
        and step.get("with", {}).get("persist-credentials") != "false"
    ]
    assert leaking == []


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_every_external_action_is_pinned_to_a_commit(name):
    """A moving tag is a supply-chain hole. Local composite actions cannot be
    pinned and are the only exemption."""
    unpinned = [
        uses
        for step in _steps(_workflow(WORKFLOWS / name))
        if (uses := step.get("uses")) and not uses.startswith("./") and not _SHA.search(uses)
    ]
    assert unpinned == []


def test_the_workflow_grants_no_write_permission_by_default():
    assert _workflow(CI)["permissions"] == {"contents": "read"}


def test_only_pull_request_runs_are_cancelled_when_superseded():
    """A push to a release branch and the weekly schedule are read after the
    fact, so cancelling them loses the answer."""
    concurrency = _workflow(CI)["concurrency"]
    assert "github.event_name == 'pull_request'" in concurrency["cancel-in-progress"]


def test_the_external_plugin_contract_job_cannot_pass_by_skipping():
    """The contract test skips when the plugin is absent. In this job that would
    be a green result over five tests that never ran, so the job sets the
    variable that turns the skip into a failure."""
    steps = _workflow(CI)["jobs"]["external-plugin-contract"]["steps"]
    required = [s for s in steps if s.get("env", {}).get("ADDUCE_REQUIRE_EXTERNAL_PLUGIN") == "1"]
    assert len(required) == 1
