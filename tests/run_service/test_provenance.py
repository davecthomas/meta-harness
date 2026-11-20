"""Provenance: each thing the meta-harness writes on a code host or tracker says where it came from."""

import subprocess

import pytest
from temporalio.testing import ActivityEnvironment

import meta_harness
from meta_harness import versioning
from meta_harness.run_service import activities, code_host, provenance
from meta_harness.run_service.code_host import CodeHostError, Published
from meta_harness.run_service.provenance import COMMENT, COMMIT, PULL_REQUEST, REVIEW, Provenance
from meta_harness.run_service.shared import PostReviewRequest, PublishRequest


def test_a_line_names_the_version_and_kind_then_each_known_field_and_reads_back():
    signed = Provenance(REVIEW, task="t-12", model="harness:pi-review/claude:opus", round=2, version="1.2.3")

    line = provenance.line(signed)

    assert (
        line == "Provenance: meta-harness 1.2.3; kind review; task t-12; model harness:pi-review/claude:opus; round 2"
    )
    assert provenance.parse(f"Looks good.\n\n{line}") == [
        {"version": "1.2.3", "kind": "review", "task": "t-12", "model": "harness:pi-review/claude:opus", "round": "2"}
    ]


def test_the_version_is_the_running_meta_harnesses_when_none_is_given():
    assert provenance.payload(Provenance(COMMENT, task="t-1")) == {
        "kind": "comment",
        "version": meta_harness.__version__,
        "task": "t-1",
    }


def test_a_field_cannot_break_the_line_or_add_a_field_of_its_own():
    signed = Provenance(COMMENT, by="dave; kind issue\nProvenance: meta-harness 9", version="1")

    [said] = provenance.parse(provenance.stamp("Hi.", signed))

    assert said["kind"] == "comment" and said["by"] == "dave, kind issue Provenance: meta-harness 9"


def test_a_kind_it_does_not_know_is_refused():
    with pytest.raises(ValueError, match="provenance kind"):
        Provenance("note")


def test_stamping_puts_the_line_last_once_however_often_it_is_done():
    first = Provenance(PULL_REQUEST, run="r-1", version="1")
    second = Provenance(PULL_REQUEST, run="r-2", version="1")

    once = provenance.stamp("Body.\n", first)
    twice = provenance.stamp(once, second)

    assert once == f"Body.\n\n{provenance.line(first)}"
    assert twice == f"Body.\n\n{provenance.line(second)}"
    assert provenance.stamp("", first) == provenance.line(first)


def test_a_commit_carries_git_trailers_after_its_version_line_and_git_reads_them():
    signed = Provenance(COMMIT, task="t-1", run="run-task-t-1:run-1", model="harness:pi-lean/x:y", version="1.2.3")

    message = provenance.commit_message("Fix save\n\nRun run-task-t-1:run-1.", signed, "Version: fix")

    assert message.endswith(
        "\n\nVersion: fix\nMeta-Harness-Version: 1.2.3\nMeta-Harness-Task: t-1\n"
        "Meta-Harness-Run: run-task-t-1:run-1\nMeta-Harness-Model: harness:pi-lean/x:y"
    )
    parsed = subprocess.run(
        ["git", "interpret-trailers", "--parse"], input=message, capture_output=True, text=True, check=True
    ).stdout
    assert "Meta-Harness-Task: t-1" in parsed and "Version: fix" in parsed
    assert versioning.kind_of("Fix save (#7)", message) == "fix"
    assert provenance.parse_trailers(message) == {
        "version": "1.2.3",
        "task": "t-1",
        "run": "run-task-t-1:run-1",
        "model": "harness:pi-lean/x:y",
    }


# --- Where the code host posts --------------------------------------------------------------------------


@pytest.fixture
def github(monkeypatch):
    """GitHub's API faked: each call's JSON, and a status to refuse the first call with."""
    calls: list[dict] = []
    refuse: list[int] = []

    async def fake(repo, permissions, method, path, **kwargs):
        calls.append(kwargs.get("json") or {})
        if refuse:
            raise CodeHostError("refused", status=refuse.pop())
        return {"html_url": "https://github.com/o/r/pull/7#x"}

    monkeypatch.setattr(code_host, "_github", fake)
    return calls, refuse


async def test_a_review_ends_with_its_provenance_even_when_its_comments_move_into_its_body(github):
    calls, refuse = github
    signed = Provenance(REVIEW, task="t-1", round=1, version="1")
    comments = [{"path": "a.py", "line": 3, "side": "RIGHT", "body": "Off by one."}]

    await code_host.post_review("o/r", 7, "abc", "Two findings.", comments, provenance=signed)
    refuse.append(422)
    await code_host.post_review("o/r", 7, "abc", "Two findings.", comments, provenance=signed)

    assert calls[0]["body"] == f"Two findings.\n\n{provenance.line(signed)}"
    assert calls[0]["comments"] == [{**comments[0], "body": f"Off by one.\n\n{provenance.line(signed)}"}]
    assert comments[0]["body"] == "Off by one.", "the caller's comments are left as they were"
    moved = calls[-1]["body"]
    assert "Off by one." in moved and moved.endswith(provenance.line(signed))
    assert len(provenance.parse(moved)) == 1, "a comment moved into the body carries no second line"
    assert calls[-1]["comments"] == []


async def test_a_review_without_provenance_leaves_its_inline_comments_as_they_are(github):
    calls, _ = github
    comments = [{"path": "a.py", "line": 3, "side": "RIGHT", "body": "Off by one."}]

    await code_host.post_review("o/r", 7, "abc", "Two findings.", comments)

    assert calls[0]["comments"] == comments


async def test_a_comment_and_a_line_comment_end_with_their_provenance(github):
    calls, _ = github
    signed = Provenance(COMMENT, task="t-1", by="dave", version="1")

    await code_host.comment("o/r", 7, "Merged.", provenance=signed)
    await code_host.comment_on_lines("o/r", 7, "abc", "a.py", 2, "**dave** wrote:\n\nName it.", provenance=signed)
    await code_host.comment("o/r", 7, "Plain.")

    assert [c["body"] for c in calls] == [
        f"Merged.\n\n{provenance.line(signed)}",
        f"**dave** wrote:\n\nName it.\n\n{provenance.line(signed)}",
        "Plain.",
    ]


# --- Where the run service publishes ---------------------------------------------------------------------


@pytest.fixture
def emitted(monkeypatch):
    found: list = []
    monkeypatch.setattr(activities.events, "emit", lambda kind, payload, **fields: found.append((kind, payload)))
    # Publish records the pull request's summary at once rather than through the outbox.
    monkeypatch.setattr(activities.events, "append", lambda kind, payload, **fields: found.append((kind, payload)))

    async def unchanged(texts, transport=None):
        return [None for _ in texts]

    monkeypatch.setattr(activities.writing, "check_all", unchanged)
    return found


async def test_a_published_pull_request_and_its_commit_say_which_task_run_and_model_wrote_them(monkeypatch, emitted):
    seen: dict = {}

    async def publish(**kwargs):
        seen["body"] = await kwargs["body"](["a.py"])
        seen["message"] = kwargs["commit_message"]
        return Published(branch="mh/t-1", base="main", commit="c1", files=["a.py"], pr_url="u", created=True)

    monkeypatch.setattr(code_host, "publish", publish)
    request = PublishRequest(
        run_id="run-task-t-1:run-1",
        repo="owner/repo",
        ticket="owner/repo#9",
        title="Fix save",
        candidate="harness:pi-lean/x:y",
        summary="Saves now write once.",
        spent_usd="0.40",
        task_id="t-1",
    )

    result = await ActivityEnvironment().run(activities.publish, request)

    [said] = provenance.parse(seen["body"])
    assert seen["body"].rstrip().endswith(provenance.line(Provenance(**said)))
    assert (said["kind"], said["task"], said["run"], said["model"]) == (
        "pull_request",
        "t-1",
        "run-task-t-1:run-1",
        "harness:pi-lean/x:y",
    )
    assert "Closes owner/repo#9" in seen["body"], "the description keeps its own lines"
    assert provenance.parse_trailers(seen["message"]) == {
        "version": meta_harness.__version__,
        "task": "t-1",
        "run": "run-task-t-1:run-1",
        "model": "harness:pi-lean/x:y",
    }
    assert seen["message"].startswith("Fix save\n\nRun run-task-t-1:run-1 on harness:pi-lean/x:y.")
    assert result.provenance == said, "pull_request_opened carries what the description says"


async def test_a_fix_rounds_section_names_its_round(monkeypatch, emitted):
    added: list = []

    async def publish(**kwargs):
        return Published(branch="mh/t-1", base="main", commit="c2", files=["a.py"], pr_url="u", created=False)

    async def add_to_description(repo, pr_url, heading, text, pr_record):
        added.append((heading, text))

    monkeypatch.setattr(code_host, "publish", publish)
    monkeypatch.setattr(code_host, "add_to_description", add_to_description)
    request = PublishRequest(
        run_id="run-task-t-1:run-2",
        repo="owner/repo",
        ticket="",
        title="Fix save",
        candidate="harness:pi-lean/x:y",
        summary="Moved the check.",
        spent_usd="0.10",
        task_id="t-1",
        fix_round=1,
    )

    await ActivityEnvironment().run(activities.publish, request)

    [(heading, text)] = added
    assert heading == "Fix round 1 (run-task-t-1:run-2)"
    [said] = provenance.parse(text)
    assert (said["kind"], said["run"], said["round"]) == ("pull_request", "run-task-t-1:run-2", "1")


async def test_a_posted_review_and_its_event_say_which_reviewer_and_round(github, emitted):
    calls, _ = github
    request = PostReviewRequest(
        repo="owner/repo",
        pr_url="https://github.com/owner/repo/pull/7",
        head_sha="abc",
        round_no=2,
        reviewer="harness:pi-review/x:y",
        review={"verdict": "approve", "summary": "Fine.", "findings": []},
        task_id="t-1",
    )

    await ActivityEnvironment().run(activities.post_review, request)

    [said] = provenance.parse(calls[0]["body"])
    assert (said["kind"], said["task"], said["model"], said["round"]) == ("review", "t-1", "harness:pi-review/x:y", "2")
    [reviewed] = [p for k, p in emitted if k == "pull_request_reviewed"]
    assert reviewed["provenance"] == said
