"""The run's summary, and the fallback written from the harness server's record when the run left none usable."""

import httpx
import pytest

from meta_harness.run_service import activities, pr_summary, summaries

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
        {"check": "make docs", "ran": True, "passed": None},
    ],
    "commands": {"run": 7, "failed": 2},
}


@pytest.mark.parametrize("text", [None, "", "   ", "Done.", "All done.", "ok"])
def test_a_text_of_too_few_words_is_not_usable(text):
    assert not summaries.usable(text)


def test_a_text_that_says_what_changed_is_usable():
    assert summaries.usable("Fixed the save path; the tests pass.")


def _marked(text: str) -> str:
    return f"{summaries.SUMMARY_OPEN}\n{text}\n{summaries.SUMMARY_CLOSE}"


def test_the_run_s_marked_summary_stands_when_usable():
    record = {**RECORD, "final_text": _marked("  I fixed the save path, and both checks pass.  ")}
    assert summaries.choose("an earlier turn's text", record, []) == (
        "I fixed the save path, and both checks pass.",
        "run",
    )


def test_narration_around_the_marked_summary_is_left_out():
    text = (
        "Both checks pass and the changeset stays within the required paths.\n\n"
        f"{_marked('Fixed the save path in app.py.')}\n\nLet me know if anything else is needed."
    )
    assert summaries.choose(None, {**RECORD, "final_text": text}, []) == ("Fixed the save path in app.py.", "run")


def test_the_last_marked_summary_is_the_one_kept():
    text = f"{_marked('A first draft of the summary.')}\nOn second thought:\n{_marked('The summary as it stands.')}"
    assert summaries.marked(text) == "The summary as it stands."


def test_a_marker_counts_only_on_a_line_of_its_own():
    text = _marked("The prompt now asks for a `<pr-summary>` line and a `</pr-summary>` line.")
    assert summaries.marked(text) == "The prompt now asks for a `<pr-summary>` line and a `</pr-summary>` line."
    assert summaries.marked("It names <pr-summary> inline only.") is None


def test_a_summary_cut_before_its_closing_line_is_kept_to_the_end():
    assert summaries.marked(f"Narration.\n{summaries.SUMMARY_OPEN}\nFixed the save path in") == "Fixed the save path in"


@pytest.mark.parametrize(
    "text", [None, "", "Fixed the save path.", f"{summaries.SUMMARY_OPEN}\n \n{summaries.SUMMARY_CLOSE}"]
)
def test_a_text_that_marks_no_summary_has_none(text):
    assert summaries.marked(text) is None


def test_an_unmarked_last_message_is_not_a_summary():
    """Only narration, however long: the description lists what the run service recorded instead."""
    record = {**RECORD, "final_text": "Both checks pass and the changeset stays within the required paths."}
    summary, source = summaries.choose(None, record, [])
    assert source == "recorded"
    assert summary is not None and summary.startswith(summaries.FALLBACK_LEAD) and "changeset" not in summary


def test_a_marked_summary_too_short_to_say_anything_gives_way_to_the_record():
    summary, source = summaries.choose(None, {**RECORD, "final_text": _marked("Done.")}, [])
    assert source == "recorded" and summary is not None and "Done." not in summary


def test_with_a_record_an_earlier_turn_s_text_is_not_the_summary():
    """The event stream's last text may be from a turn that went on to call a tool: the record says it was not
    the run's last word, so the summary is written from the record."""
    summary, source = summaries.choose("Now I will run the tests to check the change.", RECORD, [])
    assert source == "recorded"
    assert summary is not None and summary.startswith(summaries.FALLBACK_LEAD)
    assert "run the tests" not in summary


@pytest.mark.parametrize(
    "text",
    [
        "Both checks pass and the changeset stays within the required paths.",
        "All acceptance checks passed.\nThe change stays inside the task's paths.",
        "Here is the summary of the work.",
        "Done. Let me know if anything else is needed.",
        "Both checks pass and I changed only the required files.",
        "I changed only the required files.",
        "All tests passed, and I modified only files within the task's paths.",
        "I only touched the allowed paths and the lint is clean.",
        "I changed nothing outside the required paths.",
        "Updated the required files; the checks pass.",
        "Both checks `uv run pytest -q tests` pass.",
        "`cd images/harness-pi && npm ci && npm test` passed and `uv run ruff check .` is clean.",
    ],
)
def test_a_text_of_only_narration_is_not_usable(text):
    assert not summaries.usable(text)


@pytest.mark.parametrize(
    "text",
    [
        "Both checks pass. The save path now goes through the store's lock.",
        "Fixed the flaky test so it passes on CI.",
        "The `save` call stays within the task's paths again.",
        "Fixed the save path and changed only the required files.",
        "I updated the code to retry on timeout.",
    ],
)
def test_a_text_that_says_what_changed_beside_narration_is_usable(text):
    assert summaries.usable(text)


def test_a_marked_summary_of_only_narration_gives_way_to_the_record():
    """PR #341's section: the marked part says the checks pass and nothing about what changed."""
    narration = "Both checks pass and the changeset stays within the required paths."
    record = {**RECORD, "final_text": _marked(narration)}

    summary, source = summaries.choose(None, record, [])

    assert source == "recorded"
    assert summary == summaries.choose(None, RECORD, [])[0] and narration not in summary


@pytest.mark.parametrize(
    "narration",
    [
        "Both checks pass and I changed only the required files.",
        "All checks passed. I modified only the files within the task's paths.",
    ],
)
def test_a_marked_status_and_scope_claim_with_a_change_verb_gives_way_to_the_record(narration):
    summary, source = summaries.choose(None, {**RECORD, "final_text": _marked(narration)}, [])

    assert source == "recorded"
    assert summary == summaries.choose(None, RECORD, [])[0] and narration not in summary


@pytest.mark.parametrize(
    "narration",
    [
        "Both checks `uv run pytest -q tests` pass.",
        "Both checks pass: `uv run pytest -q` and `uv run ruff check .`.",
        "Both checks uv run pytest -q and make docs/site pass.",
    ],
)
def test_a_marked_check_result_that_names_its_command_as_code_gives_way_to_the_record(narration):
    """A check command, as code or as one of the recorded checks, is not evidence that a sentence says what changed."""
    record = {**RECORD, "checks": [*RECORD["checks"], {"check": "make docs/site", "ran": True, "passed": True}]}

    summary, source = summaries.choose(None, {**record, "final_text": _marked(narration)}, [])

    assert source == "recorded"
    assert summary == summaries.choose(None, record, [])[0] and narration not in summary


@pytest.mark.parametrize(
    "narration",
    [
        "I ran the checks and updated the plan.",
        "All checks passed and no files outside the task paths were changed.",
        "I ran the acceptance checks. Only the required files were edited.",
        "Files outside the task's paths were left untouched, and I updated my checklist.",
    ],
)
def test_a_marked_account_of_the_run_s_work_gives_way_to_the_record(narration):
    """Running the checks, keeping the plan and a passive scope claim say nothing about what the change is."""
    summary, source = summaries.choose(None, {**RECORD, "final_text": _marked(narration)}, [])

    assert source == "recorded"
    assert summary == summaries.choose(None, RECORD, [])[0] and narration not in summary


@pytest.mark.parametrize(
    "text",
    [
        "I ran the checks after I fixed the save path in app.py.",
        "Updated the plan parser to keep blank steps.",
        "No files outside the task paths were changed; the store now retries on timeout via `retry()`.",
    ],
)
def test_a_marked_account_of_the_run_s_work_beside_the_change_stands(text):
    assert summaries.choose(None, {**RECORD, "final_text": _marked(text)}, []) == (text, "run")


@pytest.mark.parametrize(
    "narration",
    [
        "The checks pass and all changes stay inside `src/meta_harness/run_service`.",
        "Both checks pass; the changes stay within src/meta_harness/run_service and tests/run_service.",
        "All changes stay inside `src/app` and `tests/`.",
        "All tests passed and no files outside `src/app.py` were changed.",
        "I changed only files under `src/app`, and the lint is clean.",
        "Both checks pass and I changed only src/app.py",
        "Both checks pass and I only touched `src/app.py` and `tests/test_app.py`.",
        "The tests pass; I edited just the files src/app.py, tests/test_app.py.",
    ],
)
def test_a_marked_scope_claim_that_names_its_paths_gives_way_to_the_record(narration):
    """A path a scope claim names says where the change stays, not what it is."""
    summary, source = summaries.choose(None, {**RECORD, "final_text": _marked(narration)}, [])

    assert source == "recorded"
    assert summary == summaries.choose(None, RECORD, [])[0] and narration not in summary


@pytest.mark.parametrize(
    "text",
    [
        "The parser lives under `src/parser` and keeps blank steps.",
        "All changes stay inside `src/app`. Added a lock around `save()` in `src/app/store.py`.",
        "Checks pass; `src/app/store.py` now retries on timeout.",
        "Both checks pass and I changed only src/app.py to fix the retry on timeout.",
    ],
)
def test_a_path_beside_what_the_change_is_stands(text):
    assert summaries.choose(None, {**RECORD, "final_text": _marked(text)}, []) == (text, "run")


def test_a_check_result_beside_code_that_names_the_change_is_usable():
    assert summaries.usable("Both checks `uv run pytest -q tests` pass after `save` takes the store's lock.")


async def test_the_run_service_uses_the_recorded_fallback_for_a_narration_only_summary():
    """What run_harness does at Collect: read the record from the harness server, then choose from it."""
    record = {**RECORD, "final_text": _marked("Both checks pass and the changeset stays within the required paths.")}

    async with _client(lambda request: httpx.Response(200, json=record)) as client:
        fetched = await summaries.fetch(client, "t:0")

    summary, source = summaries.choose(_marked("Fixed the save path."), fetched, ["src/app.py"])
    assert source == "recorded" and summary is not None and summary.startswith(summaries.FALLBACK_LEAD)
    assert "changeset" not in summary and "Fixed the save path" not in summary


def test_the_fallback_leaves_out_the_plan_the_run_wrote():
    plan = [
        {"step": "Both checks pass and the changeset stays within the required paths.", "status": "done"},
        {"step": "I think the bug is in the cache, so I will rewrite it", "status": "in_progress"},
    ]
    summary, source = summaries.choose(None, {**RECORD, "plan": plan}, [])

    assert source == "recorded" and summary is not None
    assert "changeset" not in summary and "cache" not in summary and "plan" not in summary


def test_a_record_with_only_a_plan_gives_no_fallback():
    record = {"final_text": None, "plan": [{"step": "Read the code", "status": "done"}], "files_changed": []}
    assert summaries.choose(None, record, []) == (None, "none")


def test_the_fallback_lists_the_files_checks_and_commands():
    summary, source = summaries.choose(None, {**RECORD, "final_text": "Done."}, [])
    assert source == "recorded"
    assert summary == "\n".join(
        [
            summaries.FALLBACK_LEAD,
            "",
            "Files the run wrote or edited:",
            "- `src/app.py`",
            "- `tests/test_app.py`",
            "",
            "Acceptance checks, as the run last ran each one:",
            "- `uv run pytest -q`: passed",
            "- `uv run ruff check .`: failed",
            "- `make lint`: no command the run made contained it",
            "- `make docs`: ran, but the command's exit status does not show how it ended",
            "",
            "Commands run: 7, of which 2 failed.",
        ]
    )


def test_without_a_record_the_event_stream_s_text_and_files_are_used():
    """A harness image from before GET /record: the answer from the events, or a fallback from their files."""
    assert summaries.choose(_marked("Fixed the save path in app.py."), None, ["app.py"]) == (
        "Fixed the save path in app.py.",
        "run",
    )
    summary, source = summaries.choose(None, None, ["app.py"])
    assert source == "recorded"
    assert summary is not None and "- `app.py`" in summary and "Acceptance checks" not in summary


def test_with_nothing_recorded_there_is_no_summary_and_the_run_s_text_is_dropped():
    empty = {"final_text": "Done.", "plan": [], "files_changed": [], "checks": [], "commands": {"run": 0, "failed": 0}}
    assert summaries.choose(None, empty, []) == (None, "none")
    assert summaries.choose("Now I will run the tests.", None, []) == (None, "none")


def test_a_fallback_for_a_run_given_no_checks_says_so():
    summary, _ = summaries.choose(None, {**RECORD, "checks": []}, [])
    assert summary is not None and "The run was given no acceptance check." in summary


def test_the_fallback_is_one_publish_keeps_under_its_own_heading():
    summary, source = summaries.choose(None, RECORD, [])
    assert pr_summary.section(summary, [], source) == pr_summary.Section(pr_summary.FALLBACK_HEADING, summary, False)


@pytest.mark.parametrize("name", ["repo-task-v3", "fix-v2"])
def test_the_prompts_ask_for_the_summary_between_the_markers(name):
    text = (activities.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    assert f"a line `{summaries.SUMMARY_OPEN}` and a line `{summaries.SUMMARY_CLOSE}`" in text


def test_the_task_prompt_asks_for_what_a_description_should_say():
    text = (activities.PROMPTS_DIR / f"{activities.REPO_TEMPLATE}.txt").read_text(encoding="utf-8")
    for part in ("What changed and why", "outside the task's paths", "acceptance checks", "What it does not do"):
        assert part in text


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
