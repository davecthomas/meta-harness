"""The summary in a pull request's description: the run's own after the writing pass, or the fallback."""

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, code_host, pr_summary, writing
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


def test_a_summary_too_short_to_say_anything_is_quoted_in_the_fallback():
    part = pr_summary.section("Done.", FILES)

    assert part.heading == pr_summary.FALLBACK_HEADING
    assert part.text.startswith(pr_summary.FALLBACK_LEAD) and "The run's last words: Done." in part.text


def test_a_fallback_the_run_service_already_wrote_is_kept_as_it_is():
    recorded = (
        f"{pr_summary.FALLBACK_PREFIX} from what the harness recorded.\n\n"
        "Acceptance checks, as the run last ran each one:\n- `make test`: passed"
    )

    part = pr_summary.section(recorded, FILES)

    assert part == pr_summary.Section(pr_summary.FALLBACK_HEADING, recorded, from_run=False)


def test_a_long_file_list_is_cut_with_a_count_of_the_rest():
    files = [f"f{i}.py" for i in range(pr_summary.MAX_FILES + 5)]

    text = pr_summary.fallback("", files)

    assert "- and 5 more" in text and f"f{pr_summary.MAX_FILES}.py" not in text


def test_a_fallback_with_no_files_says_so():
    assert "could not list the files" in pr_summary.fallback("", [])


# --- The publish activity ----------------------------------------------------------------------------


@pytest.fixture
def host(monkeypatch):
    """code_host's publish and add_to_description faked, and the writing pass recorded with no model call."""
    seen: dict = {"bodies": [], "added": [], "checked": [], "emitted": []}

    async def publish(*, body, **kwargs):
        if not seen["created"]:
            return code_host.Published("mh/t-1", "main", "c1", FILES, "https://github.com/o/r/pull/7", False)
        seen["bodies"].append(body if isinstance(body, str) else await body(FILES))
        return code_host.Published("mh/t-1", "main", "c1", FILES, "https://github.com/o/r/pull/7", True)

    async def add_to_description(repo, pr_url, heading, text, pr_record):
        seen["added"].append((heading, text))

    async def check_all(texts, transport=None):
        seen["checked"].append(list(texts))
        return [None for _ in texts]

    seen["created"] = True
    monkeypatch.setattr(code_host, "publish", publish)
    monkeypatch.setattr(code_host, "add_to_description", add_to_description)
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


async def test_a_fix_round_with_no_usable_summary_adds_the_fallback_to_the_description(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request("Done.", fix_round=2))

    [(heading, text)] = host["added"]
    assert heading == "Fix round 2 (run-task-t-1:run-1)"
    assert text.startswith(pr_summary.FALLBACK_LEAD) and "The run's last words: Done." in text
    assert host["checked"] == []


async def test_a_fix_round_s_own_summary_is_added_after_the_writing_pass(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request(OWN, fix_round=1))

    assert host["added"] == [("Fix round 1 (run-task-t-1:run-1)", OWN)]
    assert host["checked"] == [[OWN]]


# --- Where the summary came from ---------------------------------------------------------------------


def _summaries(host) -> list[dict]:
    return [payload for event_type, payload in host["emitted"] if event_type == "pull_request_summary"]


def test_each_summary_says_where_it_came_from():
    recorded = f"{pr_summary.FALLBACK_PREFIX} from what the harness recorded.\n\nCommands run: 3, of which 0 failed."

    assert pr_summary.section(OWN, FILES).source == "run"
    assert pr_summary.section(recorded, FILES).source == "recorded"
    assert pr_summary.section("Done.", FILES).source == "files"
    assert {pr_summary.section(t, FILES).source for t in (OWN, recorded, None)} == set(pr_summary.SOURCES)


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


async def test_a_fix_round_records_its_summary_with_its_round(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request("Done.", fix_round=2))

    [summary] = _summaries(host)
    assert (summary["fix_round"], summary["source"]) == (2, "files")


async def test_a_retried_publish_that_finds_its_pull_request_records_no_second_summary(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request(OWN))

    assert _summaries(host) == []


def test_the_summary_event_is_registered_with_every_source():
    from meta_harness.events.types import by_key

    schema = by_key()[("pull_request_summary", 1)].json_schema
    assert schema["properties"]["source"]["enum"] == list(pr_summary.SOURCES)
