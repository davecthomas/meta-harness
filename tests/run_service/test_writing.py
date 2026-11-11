"""The writing pass: the shipped scanner run for real, and the HTTP Gateway faked. No test calls a model."""

import json

import httpx
import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.config import store as config_store
from meta_harness.run_service import activities, code_host, writing
from meta_harness.run_service.shared import CommentRequest, PostReviewRequest

SLOPPY = (
    "## Summary\n\n"
    "This is a robust and seamless fix that weaves a rich tapestry around `save()`. "
    "It changed 3 files. See https://example.com/docs.\n"
)
PLAIN = "## Summary\n\nThe fix makes `save()` write the file once. It changed 3 files. See https://example.com/docs.\n"


@pytest.fixture(autouse=True)
def shipped_configuration(monkeypatch):
    """The shipped skills and task policy, whatever the machine's configuration source is."""
    monkeypatch.delenv(config_store.SOURCE_ENV, raising=False)
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "key")


def _gateway(answer: str | None, status: int = 200, asked: list | None = None) -> httpx.MockTransport:
    """A HTTP Gateway whose model rewrites every text as `answer`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(json.loads(request.content))
        assert request.url.path == "/v1/structured" and request.headers["Authorization"] == "Bearer key"
        if status != 200:
            return httpx.Response(status, text="no")
        return httpx.Response(200, json={"finish_reason": "complete", "data": {"text": answer}, "usd_cost": "0.002"})

    return httpx.MockTransport(handler)


def test_the_shipped_scanner_decides_findings_in_sloppy_text_and_none_in_plain_text():
    found = writing.decided(writing.scan(SLOPPY))

    assert {hit["match"] for hit in found} >= {"robust", "seamless", "rich tapestry"}
    assert writing.decided(writing.scan(PLAIN)) == []


def test_a_rewrite_must_keep_headings_links_code_numbers_and_the_lines_the_meta_harness_reads():
    issue = "Add the export.\n\nPaths: src/a, tests\n\n## Acceptance checks\n\n- uv run pytest -q tests\n"

    assert writing.lost(SLOPPY, PLAIN) == ""
    assert "code span" in writing.lost(SLOPPY, PLAIN.replace("`save()`", "save"))
    assert "heading" in writing.lost(SLOPPY, PLAIN.replace("## Summary", "Summary"))
    assert "link" in writing.lost(SLOPPY, PLAIN.replace("https://example.com/docs", "the docs"))
    assert "number" in writing.lost(SLOPPY, PLAIN.replace("3 files", "three files"))
    assert "reads" in writing.lost(issue, issue.replace("Paths: src/a, tests", "Paths: src/a"))
    assert "reads" in writing.lost(issue, issue.replace("- uv run pytest -q tests", "- run the tests"))
    fenced = "Run it:\n\n```\nmake test\n```\n"
    assert "code block" in writing.lost(fenced, "Run it:\n\n```\nmake tests\n```\n")


async def test_text_with_findings_is_rewritten_once_and_the_clean_rewrite_is_kept():
    asked: list = []

    outcome = await writing.check(SLOPPY, _gateway(PLAIN, asked=asked))

    assert outcome.kept and outcome.rewritten and outcome.text == PLAIN.strip() and outcome.before == SLOPPY
    assert outcome.findings >= 3 and outcome.findings_after == 0 and outcome.usd_cost == "0.002"
    assert len(asked) == 1 and asked[0]["model"] == writing.settings()["model"]
    assert "rich tapestry" in asked[0]["prompt"], "the model is told what the scanner found"


async def test_plain_text_is_posted_as_it_is_with_no_model_call():
    asked: list = []

    outcome = await writing.check(PLAIN, _gateway(PLAIN, asked=asked))

    assert outcome.text == PLAIN and outcome.findings == 0 and not outcome.rewritten and asked == []


@pytest.mark.parametrize(
    ("answer", "status", "reason"),
    [
        (SLOPPY.replace("robust and ", ""), 200, "still has"),
        (PLAIN.replace("`save()`", "the save function"), 200, "lost a code span"),
        (None, 503, "answered 503"),
        ("", 200, "wrote nothing"),
    ],
)
async def test_a_rewrite_that_fails_its_checks_is_dropped_and_the_original_is_posted(answer, status, reason):
    outcome = await writing.check(SLOPPY, _gateway(answer, status))

    assert outcome.text == SLOPPY and not outcome.kept and reason in outcome.reason


async def test_a_scanner_that_cannot_run_or_a_missing_gateway_leaves_the_original(monkeypatch):
    monkeypatch.delenv("GATEWAY_API_KEY")
    no_gateway = await writing.check(SLOPPY)
    assert no_gateway.text == SLOPPY and "GATEWAY_URL and GATEWAY_API_KEY" in no_gateway.reason

    monkeypatch.setattr(writing, "scanner_source", lambda: "raise SystemExit(3)")
    broken = await writing.check(SLOPPY)
    assert broken.text == SLOPPY and "scanner" in broken.reason


async def test_the_task_policy_turns_the_pass_off(monkeypatch):
    policy = config_store.current_document("task_policy")
    monkeypatch.setattr(
        config_store,
        "current_document",
        lambda kind: {**policy, "writing_pass": False} if kind == "task_policy" else {},
    )

    assert await writing.check(SLOPPY, _gateway(PLAIN)) is None
    assert writing.record("comment", [None]) is None, "nothing looked at, nothing recorded"


async def test_one_posts_texts_are_recorded_together_with_only_the_changed_ones_carried():
    outcomes = await writing.check_all([SLOPPY, PLAIN, ""], _gateway(PLAIN))

    payload = writing.record("review", outcomes, round=2)

    assert [o.text if o else None for o in outcomes] == [PLAIN.strip(), PLAIN, None]
    assert (payload["kind"], payload["texts"], payload["rewritten"], payload["kept"]) == ("review", 2, 1, 1)
    assert payload["changes"] == [{"before": SLOPPY, "after": PLAIN.strip()}]
    assert payload["usd_cost"] == "0.002" and payload["round"] == 2 and payload["reasons"] == []
    assert payload["model"] == f"{writing.settings()['engine']}:{writing.settings()['model']}"


def test_the_settings_are_checked_and_a_bad_one_is_named(monkeypatch):
    writing.check_settings()
    good = dict(writing.settings())
    for key, bad in (("model", ""), ("scanner", None), ("max_response_tokens", 0)):
        monkeypatch.setattr(writing, "settings", lambda key=key, bad=bad: {**good, key: bad})
        with pytest.raises(RuntimeError, match=f"writing_pass.{key}"):
            writing.check_settings()


# --- Where the run service posts ----------------------------------------------------------------------


@pytest.fixture
def passed(monkeypatch):
    """The pass with its model call faked, and what the activities record."""
    emitted: list = []
    check_all = writing.check_all

    async def with_gateway(texts, transport=None):
        return await check_all(texts, _gateway(PLAIN))

    monkeypatch.setattr(writing, "check_all", with_gateway)
    monkeypatch.setattr(
        activities.events, "emit", lambda kind, payload, **fields: emitted.append((kind, payload, fields))
    )
    return emitted


async def test_a_posted_review_carries_the_checked_summary_and_findings_and_records_the_pass(monkeypatch, passed):
    posted = []

    async def github(repo, permissions, method, path, **kwargs):
        posted.append(kwargs["json"])
        return {"html_url": "https://github.com/owner/repo/pull/7#pullrequestreview-1"}

    monkeypatch.setattr(code_host, "_github", github)
    review = {
        "verdict": "changes",
        "summary": SLOPPY,
        "findings": [
            {"path": "app.py", "line": 3, "body": "Off by one."},
            {"path": None, "line": None, "body": SLOPPY},
        ],
    }
    request = PostReviewRequest(
        repo="owner/repo",
        pr_url="https://github.com/owner/repo/pull/7",
        head_sha="abc",
        round_no=1,
        reviewer="harness:pi-review/x:y",
        review=review,
        task_id="t-1",
    )

    await ActivityEnvironment().run(activities.post_review, request)

    assert "rich tapestry" not in posted[0]["body"] and PLAIN.strip() in posted[0]["body"]
    assert posted[0]["comments"][0]["body"] == "Off by one."
    assert review["summary"] == SLOPPY, "the fix run still gets the review as the run wrote it"
    [(kind, payload, fields)] = [e for e in passed if e[0] == "writing_checked"]
    assert (payload["kind"], payload["texts"], payload["kept"], payload["round"]) == ("review", 3, 2, 1)
    assert fields["task_id"] == "t-1" and fields["event_id"]


async def test_a_comment_goes_through_the_pass_before_it_is_posted(monkeypatch, passed):
    posted = []

    async def comment(repo, number, body):
        posted.append(body)
        return "https://github.com/owner/repo/pull/7#issuecomment-1"

    monkeypatch.setattr(code_host, "comment", comment)
    request = CommentRequest(
        repo="owner/repo", pr_url="https://github.com/owner/repo/pull/7", body=SLOPPY, task_id="t-1"
    )

    await ActivityEnvironment().run(activities.post_comment, request)

    assert posted == [PLAIN.strip()]
    assert [p["kind"] for k, p, _ in passed if k == "writing_checked"] == ["comment"]


async def test_a_pull_request_description_keeps_the_meta_harness_lines_around_the_checked_summary(passed):
    from meta_harness.run_service.shared import PublishRequest

    request = PublishRequest(
        run_id="run-task-t-1:run-1",
        repo="owner/repo",
        ticket="owner/repo#9",
        title="Fix save",
        candidate="harness:pi-lean/x:y",
        summary=SLOPPY,
        spent_usd="0.40",
        task_id="t-1",
    )

    [summary] = await activities._written("pull_request", [request.summary], request.task_id, run_id=request.run_id)
    body = activities._pr_body(request, summary)

    assert "Closes owner/repo#9" in body and "`run-task-t-1:run-1`" in body and "$0.40" in body
    assert PLAIN.strip() in body and "rich tapestry" not in body
    [(_, payload, fields)] = passed
    assert payload["kind"] == "pull_request" and payload["run_id"] == request.run_id and fields["task_id"] == "t-1"
