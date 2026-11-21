"""A clean review is kept: the base comes in before a review, and the last clean verdict is read for a retry.

The activities run against stand-ins for the code host and the event service. No GitHub is called.
"""

from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import base_activities, code_host, resume_activities
from meta_harness.run_service.shared import BaseRequest

PR = "https://github.com/owner/repo/pull/7"


def _request() -> BaseRequest:
    return BaseRequest(task_id="t-1", repo="owner/repo", pr_url=PR, base="main", head_sha="c0")


@pytest.mark.parametrize(
    ("brought", "state", "head"),
    [(None, "current", "c0"), (("head_changed", "", "c9", "brought in 2"), "head_changed", "c9")],
)
async def test_bring_base_says_whether_the_branch_holds_the_base_or_has_a_new_head(monkeypatch, brought, state, head):
    async def bring(repo, number, base, head_sha, provenance=None):
        assert (repo, number, base, head_sha) == ("owner/repo", 7, "main", "c0")
        return brought

    async def same(repo, base, before, after):
        return True

    monkeypatch.setattr(code_host, "_bring_base_in", bring)
    monkeypatch.setattr(code_host, "same_diff", same)

    result = await ActivityEnvironment().run(base_activities.bring_base, _request())

    assert (result.state, result.head_sha) == (state, head)
    assert result.same_diff is (state == "head_changed")


async def test_bring_base_reports_a_conflict_for_a_fix_run_to_resolve(monkeypatch):
    async def bring(repo, number, base, head_sha, provenance=None):
        return "conflict", "", head_sha, "main is 3 commits ahead and does not merge in cleanly"

    monkeypatch.setattr(code_host, "_bring_base_in", bring)

    result = await ActivityEnvironment().run(base_activities.bring_base, _request())

    assert result.state == "conflict" and "does not merge in cleanly" in result.detail


def _reviews(monkeypatch, found):
    def read(**asked):
        assert asked["task_id"] == "t-1" and asked["event_type"] == "pull_request_reviewed" and asked["limit"] == 1
        if isinstance(found, Exception):
            raise found
        return {"events": found}

    monkeypatch.setattr(resume_activities.events, "client", lambda: SimpleNamespace(read=read))
    monkeypatch.setattr(resume_activities.activity, "logger", SimpleNamespace(warning=lambda *a: None))


def test_the_last_review_when_clean_names_the_commit_it_passed(monkeypatch):
    payload = {"round": 11, "verdict": "clean", "head_sha": "06daae279d3d", "reviewer": "harness:pi-review/a:b"}
    _reviews(monkeypatch, [{"payload": payload}])

    head, clean = resume_activities.last_clean_review("t-1")

    assert head == "06daae279d3d"
    assert clean == "review round 11 by harness:pi-review/a:b came back clean at 06daae279d3d"


@pytest.mark.parametrize(
    "found",
    [[], [{"payload": {"round": 12, "verdict": "changes", "head_sha": "e1e93b9a"}}], RuntimeError("unreachable")],
)
def test_no_clean_verdict_stands_after_changes_no_review_or_an_unread_event_service(monkeypatch, found):
    _reviews(monkeypatch, found)

    assert resume_activities.last_clean_review("t-1") == ("", "")
