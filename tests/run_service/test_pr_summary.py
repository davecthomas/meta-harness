"""The summary in a pull request's description: the run's own after the writing pass, or the fallback."""

import pytest
from temporalio.testing import ActivityEnvironment

from meta_harness.run_service import activities, code_host, pr_summary, provenance, writing
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


def test_a_fallback_the_run_service_already_wrote_is_kept_as_it_is():
    recorded = (
        f"{pr_summary.FALLBACK_PREFIX} from what the harness recorded.\n\n"
        "Acceptance checks, as the run last ran each one:\n- `make test`: passed"
    )

    part = pr_summary.section(recorded, FILES)

    assert part == pr_summary.Section(pr_summary.FALLBACK_HEADING, recorded, from_run=False)


def test_a_long_file_list_is_cut_with_a_count_of_the_rest():
    files = [f"f{i}.py" for i in range(pr_summary.MAX_FILES + 5)]

    text = pr_summary.fallback(files)

    assert "- and 5 more" in text and f"f{pr_summary.MAX_FILES}.py" not in text


def test_a_fallback_with_no_files_says_so():
    assert "could not list the files" in pr_summary.fallback([])


def test_a_fallback_written_before_the_new_prefix_is_still_kept_as_it_is():
    earlier = f"{pr_summary.EARLIER_PREFIX} from what the harness recorded.\n\n- `a.py`"

    assert pr_summary.section(earlier, FILES) == pr_summary.Section(pr_summary.FALLBACK_HEADING, earlier, False)


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
    assert text.startswith(pr_summary.FALLBACK_LEAD) and "Done." not in text
    assert host["checked"] == []


async def test_a_fix_round_s_own_summary_is_added_after_the_writing_pass(host):
    host["created"] = False

    await ActivityEnvironment().run(activities.publish, _request(OWN, fix_round=1))

    [(heading, text)] = host["added"]
    assert heading == "Fix round 1 (run-task-t-1:run-1)"
    assert text.startswith(f"{OWN}\n\n") and provenance.parse(text), "the round's section carries its provenance"
    assert host["checked"] == [[OWN]]
