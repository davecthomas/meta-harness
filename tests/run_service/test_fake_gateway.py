"""The fake HTTP Gateway's scripted runs, which the zero-cost end-to-end check depends on."""

from meta_harness.run_service import fake_gateway


def _grant(run_id: str) -> dict:
    return {"run_id": run_id, "turns": 0, "engine": "claude", "model": "m"}


def test_a_run_writes_the_file_then_says_it_is_done():
    first = fake_gateway.scripted_turn(_grant("run-task-a:run-1"), [{"role": "user", "content": "do it"}], ["write"])
    assert first["tool_calls"][0]["name"] == "write"
    assert first["tool_calls"][0]["input"]["content"] == fake_gateway.EXPECTED_TEXT
    result = {"type": "tool_result", "tool_call_id": "call_1", "result": "ok", "is_error": False}
    done = fake_gateway.scripted_turn(_grant("run-task-a:run-1"), [result])
    assert done["finish_reason"] == "complete"


def test_a_review_run_submits_a_verdict_and_a_tasks_later_reviews_are_clean(monkeypatch):
    monkeypatch.setattr(fake_gateway, "FIRST_REVIEW", "changes")
    monkeypatch.setattr(fake_gateway, "REVIEWED", set())
    tools = ["read", "bash", "submit_review"]

    first = fake_gateway.scripted_turn(_grant("run-task-b:run-2"), [], tools)["tool_calls"][0]
    later = fake_gateway.scripted_turn(_grant("run-task-b:run-4"), [], tools)["tool_calls"][0]
    other = fake_gateway.scripted_turn(_grant("run-task-c:run-2"), [], tools)["tool_calls"][0]

    assert first["name"] == "submit_review" and first["input"]["verdict"] == "changes"
    assert first["input"]["findings"], "a changes verdict carries a finding"
    assert later["input"]["verdict"] == "clean"
    assert other["input"]["verdict"] == "changes", "each task's first review gets FAKE_FIRST_REVIEW"


def test_a_fix_run_changes_the_file_so_its_publish_has_a_new_commit():
    prompt = [{"role": "user", "content": "You are fixing a pull request in the repository /tmp/origin.git."}]

    call = fake_gateway.scripted_turn(_grant("run-task-d:run-3"), prompt, ["write"])["tool_calls"][0]

    assert call["input"]["content"] == fake_gateway.FIXED_TEXT != fake_gateway.EXPECTED_TEXT
