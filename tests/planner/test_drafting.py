"""Labels counted from open issues, and a goal drafted from one label's issues by one gateway call."""

import json

import httpx
import pytest

from meta_harness.planner import drafting
from meta_harness.planner.tracker import Issue


def _issue(repo: str, number: int, labels: list[str], body: str = "Body.") -> Issue:
    return Issue(
        ref=f"github:{repo}#{number}",
        repo=repo,
        number=number,
        title=f"Issue {number}",
        body=body,
        labels=labels,
        state="open",
    )


def test_labels_are_counted_per_repo_and_in_total_most_first():
    rows = drafting.count_labels(
        {
            "o/a": [_issue("o/a", 1, ["bug", "ui"]), _issue("o/a", 2, ["bug"]), _issue("o/a", 3, [])],
            "o/b": [_issue("o/b", 1, ["bug"]), _issue("o/b", 2, ["docs", "docs"])],
        }
    )

    assert rows[0] == {"label": "bug", "total": 3, "repos": {"o/a": 2, "o/b": 1}}
    assert {r["label"]: r["total"] for r in rows[1:]} == {"docs": 1, "ui": 1}
    assert [r["label"] for r in rows[1:]] == ["docs", "ui"], "ties sort by name"


def test_the_prompt_marks_the_issues_as_data_and_cuts_long_bodies():
    prompt = drafting.draft_prompt("bug", [_issue("o/a", 1, ["bug"], body="x" * 50)], body_chars=10)

    assert "--- issues ---" in prompt and "--- end of issues ---" in prompt
    assert "## github:o/a#1: Issue 1" in prompt
    assert "x" * 10 + " [cut]" in prompt and "x" * 11 not in prompt


def _gateway(monkeypatch, answer: dict, status: int = 200):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway")
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=answer)

    return httpx.MockTransport(handler), seen


DRAFTED = {"title": "Fix the bugs", "statement": "Close every bug.", "theme": "All bugs.", "modality": "fix"}


async def test_a_draft_is_one_structured_call_with_the_configured_model(monkeypatch):
    transport, seen = _gateway(monkeypatch, {"data": DRAFTED, "finish_reason": "complete", "usd_cost": "0.0012"})

    draft = await drafting.draft_goal("bug", [_issue("o/a", 1, ["bug"])], transport=transport)

    assert (draft.title, draft.modality, draft.usd_cost) == ("Fix the bugs", "fix", "0.0012")
    config = drafting.settings()
    assert draft.model == f"{config['engine']}:{config['model']}"
    request = seen[0]
    assert str(request.url) == "http://gateway/v1/structured"
    assert request.headers["authorization"] == "Bearer gw-key"
    sent = json.loads(request.content)
    assert (sent["engine"], sent["model"]) == (config["engine"], config["model"])
    assert sent["response_schema"]["required"] == ["title", "statement", "theme", "modality"]
    assert "github:o/a#1" in sent["prompt"]


async def test_only_max_issues_go_to_the_model(monkeypatch):
    transport, seen = _gateway(monkeypatch, {"data": DRAFTED, "finish_reason": "complete"})
    monkeypatch.setitem(drafting.settings(), "max_issues", 2)

    await drafting.draft_goal("bug", [_issue("o/a", n, ["bug"]) for n in range(1, 6)], transport=transport)

    prompt = json.loads(seen[0].content)["prompt"]
    assert "#2:" in prompt and "#3:" not in prompt


@pytest.mark.parametrize(
    ("answer", "status", "message"),
    [
        ({"data": None, "finish_reason": "max_tokens"}, 200, "did not finish"),
        ({"data": {**DRAFTED, "modality": "rewrite"}, "finish_reason": "complete"}, 200, "modality"),
        ({"detail": "no"}, 503, "answered 503"),
    ],
)
async def test_a_draft_the_model_did_not_finish_or_got_wrong_is_refused(monkeypatch, answer, status, message):
    transport, _ = _gateway(monkeypatch, answer, status)

    with pytest.raises(drafting.DraftError, match=message):
        await drafting.draft_goal("bug", [_issue("o/a", 1, ["bug"])], transport=transport)


async def test_no_issues_or_no_gateway_settings_is_refused_before_any_call(monkeypatch):
    with pytest.raises(drafting.DraftError, match="no open issues"):
        await drafting.draft_goal("bug", [])
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    with pytest.raises(drafting.DraftError, match="GATEWAY_URL"):
        await drafting.draft_goal("bug", [_issue("o/a", 1, ["bug"])])


def test_malformed_drafting_settings_stop_the_planner(monkeypatch):
    monkeypatch.setitem(drafting.settings(), "max_issues", 0)
    with pytest.raises(RuntimeError, match="max_issues"):
        drafting.check_settings()
