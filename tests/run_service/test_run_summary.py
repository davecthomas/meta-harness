"""The run's summary is the text of its closing turn, never narration from a turn that called tools."""

from meta_harness.run_service import activities


def _feed(*turns: dict) -> str | None:
    progress = {"last_event_id": 0, "files_changed": [], "answer": None}
    for data in turns:
        activities._record(progress, {"type": "turn_finished", "data": data})
    return progress["answer"]


def test_closing_turn_text_is_the_summary():
    narration = {"text": "Let me look at the file.", "tool_calls": 2}
    closing = {"text": " Fixed the newline. ", "tool_calls": 0}
    assert _feed(narration, closing) == "Fixed the newline."


def test_narration_is_not_a_summary():
    assert _feed({"text": "Now I will run the tests.", "tool_calls": 1}) is None


def test_narration_after_a_summary_drops_it():
    assert _feed({"text": "Done.", "tool_calls": 0}, {"text": "One more check.", "tool_calls": 1}) is None


def test_empty_closing_turn_keeps_the_last_summary():
    assert _feed({"text": "Done.", "tool_calls": 0}, {"text": "", "tool_calls": 0}) == "Done."


def test_cut_text_is_used():
    assert _feed({"text": {"truncated": True, "preview": "Long summary"}, "tool_calls": 0}) == "Long summary"
