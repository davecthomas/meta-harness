"""The summary in a pull request's description: the run's own after the writing pass, or the fallback."""

import dataclasses

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, code_host, pr_summary, provenance, summaries, writing
from meta_harness.run_service.shared import PublishRequest

FILES = ["src/app.py", "tests/test_app.py"]
OWN = "Fixed the save path in app.py; uv run pytest -q passes."


@pytest.mark.parametrize("text", [None, "", "   ", "Done.", "All done.", "ok"])
def test_a_text_of_too_few_words_is_not_usable(text):
    assert not pr_summary.usable(text)


def test_the_run_s_own_summary_stands_when_usable():
    part = pr_summary.section(f"  {OWN}\n", FILES)

    assert part == pr_summary.Section(pr_summary.RUN_HEADING, OWN, from_run=True)


def test_a_missing_summary_gives_way_to_a_fallback_from_the_branch_s_files():
    part = pr_summary.section(None, FILES)

    assert not part.from_run and part.heading == pr_summary.FALLBACK_HEADING
    assert part.text == "\n".join(
        [pr_summary.FALLBACK_LEAD, "", "Files the branch changes:", "- `src/app.py`", "- `tests/test_app.py`"]
    )


def test_a_summary_too_short_to_say_anything_gives_way_to_a_fallback_without_the_run_s_words():
    part = pr_summary.section("Done.", FILES)

    assert part.heading == pr_summary.FALLBACK_HEADING
    assert part.text == pr_summary.fallback(FILES) and "Done." not in part.text


def test_the_headings_say_what_a_reviewer_finds_not_how_it_was_made():
    assert (pr_summary.RUN_HEADING, pr_summary.FALLBACK_HEADING) == (
        "What this change does",
        "What this change touches",
    )


def test_the_fallback_says_plainly_that_the_run_left_no_summary():
    assert pr_summary.fallback(FILES).startswith("The run left no summary of its change, so this section lists")


def test_a_fallback_the_run_service_already_wrote_is_kept_with_the_branch_s_files():
    recorded = (
        f"{pr_summary.FALLBACK_PREFIX} from what the harness recorded.\n\n"
        "Acceptance checks, as the run last ran each one:\n- `make test`: passed"
    )

    part = pr_summary.section(recorded, FILES, "recorded")

    listed = "Other files the branch changes:\n- `src/app.py`\n- `tests/test_app.py`"
    assert part == pr_summary.Section(pr_summary.FALLBACK_HEADING, f"{recorded}\n\n{listed}", from_run=False)


def test_a_long_file_list_is_cut_with_a_count_of_the_rest():
    files = [f"f{i}.py" for i in range(pr_summary.MAX_FILES + 5)]

    text = pr_summary.fallback(files)

    assert "- and 5 more" in text and f"f{pr_summary.MAX_FILES}.py" not in text


def test_a_fallback_with_no_files_says_so():
    assert "could not list the files" in pr_summary.fallback([])


def test_a_recorded_fallback_gains_the_branch_files_the_record_does_not_name():
    """A mixed run: it edited src/app.py with its edit tool, and changed tests/test_app.py with a bash command,
    which the record does not see. The branch names both."""
    recorded, source = summaries.choose(None, {"files_changed": ["src/app.py"], "checks": []}, [])
    assert recorded is not None and "tests/test_app.py" not in recorded

    part = pr_summary.section(recorded, FILES, source)

    assert part.heading == pr_summary.FALLBACK_HEADING and not part.from_run
    assert part.text == f"{recorded}\n\nOther files the branch changes:\n- `tests/test_app.py`"


def test_a_recorded_fallback_that_names_every_branch_file_is_kept_as_it_is():
    recorded, source = summaries.choose(None, {"files_changed": FILES, "checks": []}, [])

    assert pr_summary.section(recorded, FILES, source).text == recorded


def test_a_run_s_marked_summary_in_the_fallback_s_words_is_still_the_run_s():
    """The source, not the opening words, says who wrote a summary: a marked message that opens like the
    fallback and names a file is the run's own, so it goes under the run's heading and through the writing pass."""
    text = f"{pr_summary.FALLBACK_PREFIX}. It edited `src/app.py`."
    record = {"final_text": f"{summaries.SUMMARY_OPEN}\n{text}\n{summaries.SUMMARY_CLOSE}", "files_changed": FILES}
    summary, source = summaries.choose(None, record, [])
    assert (summary, source) == (text, "run")

    assert pr_summary.section(summary, FILES, source) == pr_summary.Section(pr_summary.RUN_HEADING, text, True)


@pytest.mark.parametrize("source", ["", "run"])
def test_without_the_recorded_source_the_fallback_s_words_are_not_a_fallback(source):
    text = f"{pr_summary.FALLBACK_PREFIX}. It edited `src/app.py`."

    assert pr_summary.section(text, FILES, source).from_run
    assert not pr_summary.is_fallback(text, source)


def test_a_fallback_written_before_the_new_prefix_is_still_kept_as_it_is():
    earlier = f"{pr_summary.EARLIER_PREFIX} from what the harness recorded.\n\n- `a.py`"

    assert pr_summary.section(earlier, ["a.py"]) == pr_summary.Section(pr_summary.FALLBACK_HEADING, earlier, False)


# --- The publish activity ----------------------------------------------------------------------------


@pytest.fixture
def host(monkeypatch):
    """code_host's publish, description and add_to_description faked, the writing pass recorded with no model
    call, and the events appended. A pull request already open has host["description"] as its description,
    which add_to_description adds a section to once;
    with host["label_fails"], opening one posts it and then fails, as a failed label request would."""
    seen: dict = {"bodies": [], "added": [], "checked": [], "emitted": [], "description": "", "label_fails": False}

    async def publish(*, body, **kwargs):
        if not seen["created"]:
            return code_host.Published("mh/t-1", "main", "c1", FILES, "https://github.com/o/r/pull/7", False)
        seen["bodies"].append(body if isinstance(body, str) else await body(FILES))
        if seen["label_fails"]:
            seen["created"], seen["description"] = False, seen["bodies"][-1]
            raise code_host.CodeHostError("POST /repos/o/r/issues/7/labels: 502")
        return code_host.Published("mh/t-1", "main", "c1", FILES, "https://github.com/o/r/pull/7", True)

    async def description(repo, pr_url, pr_record):
        return seen["description"]

    def append(event_type, payload, **fields):
        if seen.get("events_down"):
            seen["events_down"] -= 1
            raise httpx.ConnectError("the event service is unreachable")
        seen["emitted"].append((event_type, payload))

    async def add_to_description(repo, pr_url, heading, text, pr_record):
        seen["added"].append((heading, text))
        if f"## {heading}\n" not in seen["description"]:
            seen["description"] = seen["description"].rstrip() + f"\n\n## {heading}\n\n{text.strip()}\n"
        return seen["description"]

    async def check_all(texts, transport=None):
        seen["checked"].append(list(texts))
        return [None for _ in texts]

    seen["created"] = True
    monkeypatch.setattr(code_host, "publish", publish)
    monkeypatch.setattr(code_host, "add_to_description", add_to_description)
    monkeypatch.setattr(code_host, "description", description)
    monkeypatch.setattr(activities.events, "append", append)
    monkeypatch.setattr(writing, "check_all", check_all)
    monkeypatch.setattr(activities.events, "emit", lambda *args, **kwargs: seen["emitted"].append(args))
    return seen


def _request(summary: str | None, **extra) -> PublishRequest:
    return PublishRequest(
        run_id="run-task-t-1:run-1",
        repo="o/r",
        ticket="o/r#9",
        title="Fix save",
        candidate="harness:pi-lean/x:y",
        summary=summary,
        spent_usd="0.40",
        task_id="t-1",
        **extra,
    )


async def test_a_pull_request_carries_the_run_s_summary_after_the_writing_pass(host):
    await ActivityEnvironment().run(activities.publish, _request(OWN))

    [body] = host["bodies"]
    assert f"## {pr_summary.RUN_HEADING}\n\n{OWN}\n" in body and pr_summary.FALLBACK_HEADING not in body
    assert host["checked"] == [[OWN]]


async def test_a_pull_request_whose_run_left_no_summary_carries_the_fallback_unchecked(host):
    await ActivityEnvironment().run(activities.publish, _request(None))

    [body] = host["bodies"]
    assert f"## {pr_summary.FALLBACK_HEADING}\n\n{pr_summary.FALLBACK_LEAD}" in body
    assert "- `src/app.py`" in body and "Closes o/r#9" in body and "`run-task-t-1:run-1`" in body
    assert pr_summary.RUN_HEADING not in body
    assert host["checked"] == [], "the meta-harness's own lines skip the writing pass"


async def test_a_pull_request_lists_the_files_a_command_changed_beside_the_recorded_ones(host):
    recorded, source = summaries.choose(None, {"files_changed": ["src/app.py"], "checks": []}, [])

    await ActivityEnvironment().run(activities.publish, _request(recorded, summary_source=source))

    [body] = host["bodies"]
    assert f"## {pr_summary.FALLBACK_HEADING}\n\n{recorded}\n\nOther files the branch changes:\n" in body
    assert "- `src/app.py`" in body and "- `tests/test_app.py`" in body
    assert host["checked"] == []


async def test_a_run_s_summary_in_the_fallback_s_words_goes_through_the_writing_pass(host):
    text = f"{pr_summary.FALLBACK_PREFIX}. It edited `src/app.py`."

    await ActivityEnvironment().run(activities.publish, _request(text, summary_source="run"))

    [body] = host["bodies"]
    assert f"## {pr_summary.RUN_HEADING}\n\n{text}\n" in body and pr_summary.FALLBACK_HEADING not in body
    assert host["checked"] == [[text]]


async def test_a_fix_round_with_no_usable_summary_adds_the_fallback_to_the_description(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request("Done.", fix_round=2))

    [(heading, text)] = host["added"]
    assert heading == "Fix round 2 (run-task-t-1:run-1)"
    assert text.startswith(pr_summary.FALLBACK_LEAD) and "Done." not in text
    assert host["checked"] == []


async def test_a_fix_round_s_own_summary_is_added_after_the_writing_pass(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request(OWN, fix_round=1))

    [(heading, text)] = host["added"]
    assert heading == "Fix round 1 (run-task-t-1:run-1)"
    assert text.startswith(f"{OWN}\n\n") and provenance.parse(text), "the round's section carries its provenance"
    assert host["checked"] == [[OWN]]


# --- Where the summary came from ---------------------------------------------------------------------


def _summaries(host) -> list[dict]:
    return [payload for event_type, payload in host["emitted"] if event_type == "pull_request_summary"]


def test_each_summary_says_where_it_came_from():
    recorded = f"{pr_summary.FALLBACK_PREFIX} from what the harness recorded.\n\nCommands run: 3, of which 0 failed."

    assert pr_summary.section(OWN, FILES).source == "run"
    assert pr_summary.section(recorded, FILES, "recorded").source == "recorded"
    assert pr_summary.section("Done.", FILES).source == "files"
    sources = {pr_summary.section(t, FILES, s).source for t, s in ((OWN, "run"), (recorded, "recorded"), (None, ""))}
    assert sources == set(pr_summary.SOURCES)


async def test_an_opened_pull_request_records_its_summary_and_source(host):
    await ActivityEnvironment().run(activities.publish, _request(OWN))

    assert _summaries(host) == [
        {
            "pr_url": "https://github.com/o/r/pull/7",
            "fix_round": 0,
            "source": "run",
            "heading": pr_summary.RUN_HEADING,
            "text": OWN,
        }
    ]


async def test_a_fallback_is_recorded_as_the_meta_harness_s(host):
    await ActivityEnvironment().run(activities.publish, _request(None))

    [summary] = _summaries(host)
    assert summary["source"] == "files" and summary["heading"] == pr_summary.FALLBACK_HEADING


async def test_a_fix_round_s_summary_in_the_fallback_s_words_is_recorded_as_the_run_s(host):
    host["created"] = False
    text = f"{pr_summary.FALLBACK_PREFIX}. It edited `src/app.py`."

    await ActivityEnvironment().run(activities.publish, _request(text, summary_source="run", fix_round=1))

    [summary] = _summaries(host)
    assert (summary["source"], summary["heading"], summary["text"]) == ("run", pr_summary.RUN_HEADING, text)


async def test_a_fix_round_records_its_summary_with_its_round(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request("Done.", fix_round=2))

    [summary] = _summaries(host)
    assert (summary["fix_round"], summary["source"]) == (2, "files")


async def test_a_publish_that_finds_a_pull_request_another_run_opened_records_no_summary(host):
    host["created"] = False
    earlier = dataclasses.replace(_request(OWN), run_id="run-task-t-1:run-0")
    host["description"] = activities._pr_body(earlier, pr_summary.section(OWN, FILES))

    await ActivityEnvironment().run(activities.publish, _request(OWN))

    assert _summaries(host) == []


async def test_a_retry_after_the_label_request_failed_records_the_summary_the_first_attempt_posted(host):
    host["label_fails"] = True

    with pytest.raises(ApplicationError, match="labels"):
        await ActivityEnvironment().run(activities.publish, _request("Done."))
    assert _summaries(host) == []

    result = await ActivityEnvironment().run(activities.publish, _request("Done."))

    assert not result.created and len(host["bodies"]) == 1, "the retry opens no second pull request"
    expected = pr_summary.section("Done.", FILES)
    assert _summaries(host) == [
        {
            "pr_url": "https://github.com/o/r/pull/7",
            "fix_round": 0,
            "source": "files",
            "heading": pr_summary.FALLBACK_HEADING,
            "text": expected.text,
        }
    ]


async def test_a_retry_reads_back_the_run_s_words_as_the_first_attempt_s_writing_pass_left_them(host, monkeypatch):
    rewrite = "Fixed the save path.\n\n## Notes\n\nuv run pytest -q passes."

    async def check_all(texts, transport=None):
        return [writing.Outcome(text=rewrite, findings=1, rewritten=True, kept=True)]

    monkeypatch.setattr(writing, "check_all", check_all)
    host["label_fails"] = True
    with pytest.raises(ApplicationError):
        await ActivityEnvironment().run(activities.publish, _request(OWN))

    await ActivityEnvironment().run(activities.publish, _request(OWN))

    [summary] = _summaries(host)
    assert (summary["source"], summary["heading"], summary["text"]) == ("run", pr_summary.RUN_HEADING, rewrite)


async def test_a_fix_round_s_retry_records_the_section_its_first_attempt_posted(host, monkeypatch):
    rewrites = iter(["Moved the check into save().", "The check now lives in save()."])

    async def check_all(texts, transport=None):
        return [writing.Outcome(text=next(rewrites), findings=1, rewritten=True, kept=True)]

    monkeypatch.setattr(writing, "check_all", check_all)
    host["created"], host["events_down"] = False, 1
    with pytest.raises(httpx.ConnectError):
        await ActivityEnvironment().run(activities.publish, _request(OWN, fix_round=1))

    await ActivityEnvironment().run(activities.publish, _request(OWN, fix_round=1))

    assert host["description"].count("## Fix round 1 (run-task-t-1:run-1)") == 1
    assert "Moved the check into save()." in host["description"] and "lives in save()" not in host["description"]
    [summary] = _summaries(host)
    assert (summary["fix_round"], summary["source"], summary["heading"], summary["text"]) == (
        1,
        "run",
        pr_summary.RUN_HEADING,
        "Moved the check into save().",
    ), "the event holds the text the description shows, not the retry's rewrite"


async def test_a_summary_the_event_service_did_not_store_fails_publish_and_its_retry_records_it(host):
    host["events_down"] = 1

    with pytest.raises(httpx.ConnectError):
        await ActivityEnvironment().run(activities.publish, _request(OWN))
    host["created"], host["description"] = False, host["bodies"][-1]

    await ActivityEnvironment().run(activities.publish, _request(OWN))

    [summary] = _summaries(host)
    assert (summary["fix_round"], summary["source"], summary["text"]) == (0, "run", OWN)


async def test_a_summary_the_event_service_refuses_fails_publish_for_good(host, monkeypatch):
    def refuse(event_type, payload, **fields):
        request = httpx.Request("POST", "http://events/v1/events")
        response = httpx.Response(422, text="bad payload", request=request)
        raise httpx.HTTPStatusError("422", request=request, response=response)

    monkeypatch.setattr(activities.events, "append", refuse)

    with pytest.raises(ApplicationError, match="refused pull_request_summary") as raised:
        await ActivityEnvironment().run(activities.publish, _request(OWN))
    assert raised.value.non_retryable


def test_a_posted_summary_is_read_back_from_the_description_up_to_its_run_section():
    request = _request(OWN)
    text = "Fixed it.\n\n## Details\n\nThe save path."
    body = activities._pr_body(request, pr_summary.Section(pr_summary.RUN_HEADING, text, from_run=True))
    body += "\n\n## Fix round 1 (run-task-t-1:run-3)\n\nMore.\n"

    assert pr_summary.posted(body.replace("\n", "\r\n"), activities._summary_end(request.run_id)) == (
        pr_summary.Section(pr_summary.RUN_HEADING, text, from_run=True)
    )
    assert pr_summary.posted(body, activities._summary_end("run-task-t-1:run-9")) is None
    assert pr_summary.posted("Closes o/r#9", activities._summary_end(request.run_id)) is None


@pytest.mark.parametrize(
    ("heading", "text", "source"),
    [
        (pr_summary.EARLIER_RUN_HEADING, OWN, "run"),
        (pr_summary.EARLIER_FALLBACK_HEADING, f"{pr_summary.EARLIER_LEAD}\n\n- `src/app.py`", "files"),
        (pr_summary.EARLIER_FALLBACK_HEADING, f"{pr_summary.EARLIER_PREFIX} from the record.", "recorded"),
    ],
)
def test_a_summary_under_an_earlier_heading_is_read_back(heading, text, source):
    """A pull request opened before the headings changed: a retry still finds its summary."""
    request = _request(OWN)
    body = activities._pr_body(request, pr_summary.Section(heading, text, from_run=source == "run"))

    read = pr_summary.posted(body, activities._summary_end(request.run_id))

    assert read is not None and (read.heading, read.text, read.source) == (heading, text, source)


async def test_a_retry_records_the_summary_of_a_pull_request_opened_under_the_earlier_heading(host):
    host["created"] = False
    part = pr_summary.Section(pr_summary.EARLIER_RUN_HEADING, OWN, from_run=True)
    host["description"] = activities._pr_body(_request(OWN), part)

    await ActivityEnvironment().run(activities.publish, _request(OWN))

    [summary] = _summaries(host)
    assert (summary["source"], summary["heading"], summary["text"]) == ("run", pr_summary.EARLIER_RUN_HEADING, OWN)


def test_a_new_description_uses_the_new_headings():
    assert pr_summary.section(OWN, FILES).heading == pr_summary.RUN_HEADING
    assert pr_summary.section("Done.", FILES).heading == pr_summary.FALLBACK_HEADING


def test_a_fix_round_s_summary_is_read_back_up_to_its_provenance_line():
    signed = provenance.Provenance(provenance.PULL_REQUEST, task="t-1", run="run-task-t-1:run-3", round=1)
    own = "Moved the check.\n\n## Notes\n\nuv run pytest -q passes."
    fallback = pr_summary.fallback(FILES)
    body = (
        f"## {pr_summary.RUN_HEADING}\n\nFixed it.\n\n## Run\n\n- Run: `run-task-t-1:run-1`"
        f"\n\n## Fix round 1 (run-task-t-1:run-3)\n\n{provenance.stamp(own, signed)}\n"
        f"\n\n## Fix round 2 (run-task-t-1:run-4)\n\n{provenance.stamp(fallback, signed)}\n"
    )

    run_part = pr_summary.section(OWN, FILES, "run")
    files_part = pr_summary.section("Done.", FILES)

    assert pr_summary.posted_round(body.replace("\n", "\r\n"), "Fix round 1 (run-task-t-1:run-3)", run_part) == (
        pr_summary.Section(pr_summary.RUN_HEADING, own, from_run=True)
    )
    second = pr_summary.posted_round(body, "Fix round 2 (run-task-t-1:run-4)", files_part)
    assert second == pr_summary.Section(pr_summary.FALLBACK_HEADING, fallback, from_run=False)
    assert second.source == "files"
    assert pr_summary.posted_round(body, "Fix round 3 (run-task-t-1:run-5)", run_part) is None


def test_a_fix_round_s_summary_read_back_keeps_the_source_it_was_posted_with():
    """A run's marked summary that opens with the fallback's words is still the run's when read back."""
    text = f"{pr_summary.FALLBACK_PREFIX}. It edited `src/app.py`."
    body = f"## Fix round 1 (r)\n\n{text}\n"
    part = pr_summary.section(text, FILES, "run")

    read = pr_summary.posted_round(body, "Fix round 1 (r)", part)

    assert read == pr_summary.Section(pr_summary.RUN_HEADING, text, from_run=True) and read.source == "run"


def test_the_summary_event_is_registered_with_every_source():
    from meta_harness.events.types import by_key

    schema = by_key()[("pull_request_summary", 1)].json_schema
    assert schema["properties"]["source"]["enum"] == list(pr_summary.SOURCES)
