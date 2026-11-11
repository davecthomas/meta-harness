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


def _checks_prompt() -> list[dict]:
    text = f"--- task ---\nCheck the sandbox. {fake_gateway.CHECKS_MARKER} uv sync && uv run pytest -q\n--- end of task ---"
    return [{"role": "user", "content": [{"type": "text", "text": text}]}]


def test_a_checks_run_runs_its_command_with_bash_then_records_that_it_passed():
    grant = _grant("run-task-e:run-1")
    prompt = _checks_prompt()

    first = fake_gateway.scripted_turn(grant, prompt, ["bash", "write"])
    assert first["finish_reason"] == "tool_use"
    assert first["tool_calls"][0]["name"] == "bash"
    assert first["tool_calls"][0]["input"] == {
        "command": f"uv sync && uv run pytest -q && echo {fake_gateway.CHECKS_OK}"
    }

    output = {
        "type": "tool_result",
        "tool_call_id": "call_1",
        "result": {"output": f"12 passed\n{fake_gateway.CHECKS_OK}\n"},
        "is_error": False,
    }
    second = fake_gateway.scripted_turn(grant, [*prompt, output], ["bash", "write"])["tool_calls"][0]
    assert second["name"] == "write" and second["input"]["path"] == fake_gateway.CHECKS_FILE
    assert second["input"]["content"].startswith("passed\n") and "12 passed" in second["input"]["content"]

    written = {"type": "tool_result", "tool_call_id": "call_2", "result": {"output": "ok"}, "is_error": False}
    done = fake_gateway.scripted_turn(grant, [*prompt, output, written], ["bash", "write"])
    assert done["finish_reason"] == "complete" and not done["tool_calls"]


def test_a_checks_run_whose_command_failed_records_that_with_its_output():
    prompt = _checks_prompt()
    failed = {
        "type": "tool_result",
        "tool_call_id": "call_1",
        "result": {"output": "sh: tsc: not found"},
        "is_error": True,
    }
    call = fake_gateway.scripted_turn(_grant("run-task-f:run-1"), [*prompt, failed], ["bash", "write"])["tool_calls"][0]
    assert call["input"]["content"].startswith("failed\n") and "tsc: not found" in call["input"]["content"]

    # Output that never reached the end marker is a failure too, whatever the tool said.
    cut = {"type": "tool_result", "tool_call_id": "call_1", "result": {"output": "12 passed"}, "is_error": False}
    call = fake_gateway.scripted_turn(_grant("run-task-g:run-1"), [*prompt, cut], ["bash", "write"])["tool_calls"][0]
    assert call["input"]["content"].startswith("failed\n")


def test_a_checks_tasks_review_run_still_submits_a_verdict(monkeypatch):
    monkeypatch.setattr(fake_gateway, "REVIEWED", set())
    call = fake_gateway.scripted_turn(_grant("run-task-h:run-2"), _checks_prompt(), ["read", "bash", "submit_review"])
    assert call["tool_calls"][0]["name"] == "submit_review"


def test_a_spend_limit_the_http_gateway_would_refuse_is_refused_and_the_unbounded_one_is_granted():
    from meta_harness.run_service.shared import DEFAULT_SPEND_LIMIT_USD, UNBOUNDED_SPEND_LIMIT_USD

    grants = fake_gateway.Grants()

    def issue(run_id: str, limit: str) -> int:
        body = {"run_id": run_id, "engine": "claude", "model": "m", "spend_limit_usd": limit}
        return grants.issue(body)[0]

    assert issue("r-1", "1000000.00") == 422, "seven digits before the point: the HTTP Gateway refuses it"
    assert issue("r-2", "ten") == 422
    assert issue("r-3", UNBOUNDED_SPEND_LIMIT_USD) == 200, "what a task gets under the track budget policy"
    assert issue("r-4", DEFAULT_SPEND_LIMIT_USD) == 200
