"""The run's summary, and the fallback written from the harness server's record when the run left none usable."""

import httpx
import pytest

from meta_harness.run_service import summaries

RECORD = {
    "run_id": "t:0",
    "status": "FINISHED",
    "final_text": None,
    "plan": [{"step": "Read the code", "status": "done"}, {"step": "Fix the bug", "status": "in_progress"}],
    "files_changed": ["src/app.py", "tests/test_app.py"],
    "checks": [
        {"check": "uv run pytest -q", "ran": True, "passed": True},
        {"check": "uv run ruff check .", "ran": True, "passed": False},
        {"check": "make lint", "ran": False, "passed": None},
    ],
    "commands": {"run": 7, "failed": 2},
}


@pytest.mark.parametrize("text", [None, "", "   ", "Done.", "All done.", "ok"])
def test_a_text_of_too_few_words_is_not_usable(text):
    assert not summaries.usable(text)


def test_a_text_that_says_what_changed_is_usable():
    assert summaries.usable("Fixed the save path; the tests pass.")


def test_the_run_s_own_final_words_stand_when_usable():
    record = {**RECORD, "final_text": "  I fixed the save path, and both checks pass.  "}
    assert summaries.choose("an earlier turn's text", record, []) == (
        "I fixed the save path, and both checks pass.",
        "run",
    )


def test_with_a_record_an_earlier_turn_s_text_is_not_the_summary():
    """The event stream's last text may be from a turn that went on to call a tool: the record says it was not
    the run's last word, so the summary is written from the record."""
    summary, source = summaries.choose("Now I will run the tests to check the change.", RECORD, [])
    assert source == "recorded"
    assert summary is not None and summary.startswith(summaries.FALLBACK_LEAD)
    assert "run the tests" not in summary


def test_the_fallback_lists_the_files_checks_plan_and_commands():
    summary, source = summaries.choose(None, {**RECORD, "final_text": "Done."}, [])
    assert source == "recorded"
    assert summary == "\n".join(
        [
            summaries.FALLBACK_LEAD,
            "",
            "The run's last words: Done.",
            "",
            "Files the run wrote or edited:",
            "- `src/app.py`",
            "- `tests/test_app.py`",
            "",
            "Acceptance checks, as the run last ran each one:",
            "- `uv run pytest -q`: passed",
            "- `uv run ruff check .`: failed",
            "- `make lint`: no command the run made contained it",
            "",
            "The run's last plan:",
            "- done: Read the code",
            "- in_progress: Fix the bug",
            "",
            "Commands run: 7, of which 2 failed.",
        ]
    )


def test_without_a_record_the_event_stream_s_text_and_files_are_used():
    """A harness image from before GET /record: the answer from the events, or a fallback from their files."""
    assert summaries.choose("Fixed the save path in app.py.", None, ["app.py"]) == (
        "Fixed the save path in app.py.",
        "run",
    )
    summary, source = summaries.choose(None, None, ["app.py"])
    assert source == "recorded"
    assert summary is not None and "- `app.py`" in summary and "Acceptance checks" not in summary


def test_with_nothing_recorded_the_run_s_text_stands():
    empty = {"final_text": "Done.", "plan": [], "files_changed": [], "checks": [], "commands": {"run": 0, "failed": 0}}
    assert summaries.choose(None, empty, []) == ("Done.", "none")
    assert summaries.choose(None, None, []) == (None, "none")


def test_a_malformed_record_is_read_for_what_it_holds():
    record = {"final_text": 5, "plan": "x", "files_changed": ["a.py", 3, ""], "checks": [{"ran": True}], "commands": []}
    summary, source = summaries.choose(None, record, [])
    assert source == "recorded"
    assert summary is not None and "- `a.py`" in summary and "Commands run" not in summary


def test_long_lists_are_cut_with_a_count_of_the_rest():
    record = {**RECORD, "files_changed": [f"f{i}.py" for i in range(summaries.MAX_LINES + 5)]}
    summary, _ = summaries.choose(None, record, [])
    assert summary is not None and "- and 5 more" in summary and f"f{summaries.MAX_LINES}.py" not in summary


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://harness.test", transport=httpx.MockTransport(handler))


async def test_fetch_reads_the_record_of_the_run():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/record" and request.url.params["run_id"] == "t:0"
        return httpx.Response(200, json=RECORD)

    async with _client(handler) as client:
        assert await summaries.fetch(client, "t:0") == RECORD


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"error": "No route GET /record."}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["a list"]),
    ],
)
async def test_fetch_gives_none_for_an_image_without_a_record_or_an_answer_it_cannot_read(response):
    async with _client(lambda request: response) as client:
        assert await summaries.fetch(client, "t:0") is None


async def test_fetch_gives_none_when_the_harness_server_cannot_be_reached():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone", request=request)

    async with _client(handler) as client:
        assert await summaries.fetch(client, "t:0") is None
