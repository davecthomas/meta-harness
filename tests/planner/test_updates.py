"""The planner's updates on a goal: written from its records when its state changes, and only then.

The planner's API runs against Postgres with fakes for the tracker, the Task API and the event service. The
model is a fake writer, or a mock HTTP Gateway: no model is called.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from meta_harness import db
from meta_harness.config import kinds
from meta_harness.planner import planning, updates
from meta_harness.planner.service import create_app

from .test_planner import OPERATOR, Events, FakeTasks, _no_title, migrated, registry  # noqa: F401
from .test_planning import WritingTracker, _feature, _planned, draft


class Writer:
    """Writes what it is told to, and keeps what it was given."""

    def __init__(self):
        self.calls: list[tuple[dict, dict | None]] = []
        self.fail: str | None = None

    async def __call__(self, found: dict, before: dict | None) -> updates.Written:
        if self.fail:
            raise updates.UpdateError(self.fail)
        self.calls.append((found, before))
        return updates.Written(
            headline=f"Update {len(self.calls)}",
            body="The plan is being drafted.",
            needs_you=[{"what": "Approve the plan.", "ref": ""}],
            next="Tasks start once you approve.",
            model="claude:test-model",
            usd_cost="0.0021",
        )


@pytest.fixture
def world():
    events, tracker, tasks, writer = Events(), WritingTracker(), FakeTasks(), Writer()
    app = create_app(
        events,
        tracker,
        tasks,
        operator_keys={"op-key": "dave"},
        service_keys={},
        sweep_seconds=0,
        titler=_no_title,
        update_writer=writer,
    )
    with TestClient(app) as client:
        yield client, events, tasks, writer


@pytest.fixture
def at_once(monkeypatch):
    """No wait between two updates of a goal."""
    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"min_seconds": 0}})


def _write(goal: dict, tasks, events, writer, **more) -> dict | None:
    return asyncio.run(updates.write(goal, tasks, events, writer, **more))


def _goal(client, goal_id: str) -> dict:
    return client.get(f"/v1/goals/{goal_id}", headers=OPERATOR).json()


def _found(**issue) -> dict:
    one = {
        "ref": "github:o/r#1",
        "state": "open",
        "task": "running",
        "pull_request": None,
        "held": None,
        "refused": None,
        "waiting_on_operator_for": None,
        "task_reason": "attempt 1",
        **issue,
    }
    return {"goal": {"status": "active", "spent_usd": "1.00"}, "plan": None, "issues": [one]}


def test_the_digest_changes_when_the_goal_moves_and_not_when_it_only_spends():
    before = updates.digest(_found())
    spent = _found(task_reason="attempt 2")
    spent["goal"]["spent_usd"] = "4.00"

    assert updates.digest(spent) == before, "spend and a reason's wording are no news"
    assert updates.digest(_found(task="awaiting_review")) != before
    assert updates.digest(_found(pull_request="https://github.com/o/r/pull/9")) != before
    assert updates.digest(_found(held="large")) != before
    assert updates.digest({**_found(), "goal": {"status": "met"}}) != before


def test_an_update_is_written_once_for_a_state_and_again_when_the_goal_moves(world, at_once):
    client, events, tasks, writer = world
    goal = _feature(client)

    first = _write(goal, tasks, events, writer)

    assert (first["number"], first["headline"], first["needs_you"], first["plan_version"]) == (
        1,
        "Update 1",
        [],
        None,
    ), "a plan still being drafted cannot be approved, so nothing about it is asked"
    found, before = writer.calls[0]
    assert before is None and found["goal"]["status"] == "planning" and found["plan"]["planning_task"] == "queued"
    assert (
        "goal_update_written",
        {"goal_id": goal["id"], "number": 1, "model": "claude:test-model", "usd_cost": "0.0021"},
    ) in events.events
    assert _write(goal, tasks, events, writer) is None, "nothing moved: nothing is written, and nothing spent"
    assert len(writer.calls) == 1

    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])
    second = _write(_goal(client, goal["id"]), tasks, events, writer)

    assert (second["number"], second["plan_version"]) == (2, 1), "an update keeps the plan version it is about"
    assert second["needs_you"] == [
        {"what": "Approve the plan.", "ref": "", "action": "approve_plan", "task_id": "", "pr_url": ""}
    ], "a need with no action of its own gets the one the goal's state supports"
    found, before = writer.calls[1]
    assert before["headline"] == "Update 1", "the writer is given its last update, to say what changed"
    assert (found["plan"]["version"], found["plan"]["new_tasks"], found["plan"]["approved"]) == (1, 2, False)
    shown = client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()
    assert [u["number"] for u in shown["updates"]] == [2, 1] and shown["enabled"] is True and shown["error"] is None
    assert shown["plan"] == {"version": 1, "replanning": False}, "the page holds the update against the plan now"


def test_an_updates_words_go_through_the_writing_pass_before_a_person_reads_them(world, at_once, monkeypatch):
    from meta_harness.run_service import writing

    client, events, tasks, writer = world
    goal = _feature(client)
    seen: list[str] = []

    async def check_all(texts, transport=None):
        seen.extend(texts)
        plain = "The plan is ready."
        return [
            writing.Outcome(
                text=plain, before=t, findings=1, rewritten=True, kept=True, model="claude:w", usd_cost="0.001"
            )
            if t == "The plan is being drafted."
            else writing.Outcome(text=t)
            for t in texts
        ]

    monkeypatch.setattr(writing, "check_all", check_all)
    saved = _write(goal, tasks, events, writer)

    assert saved["body"] == "The plan is ready." and saved["headline"] == "Update 1"
    assert {"Update 1", "The plan is being drafted.", "Tasks start once you approve."} <= set(seen)
    assert all(need["what"] in seen for need in saved["needs_you"])
    [event] = [e for e in events.events if e[0] == "goal_update_written"]
    passed = event[1]["writing"]
    assert (passed["kind"], passed["rewritten"], passed["kept"], passed["usd_cost"]) == ("update", 1, 1, "0.001")
    assert passed["changes"] == [{"before": "The plan is being drafted.", "after": "The plan is ready."}]


def test_two_updates_keep_their_distance_and_a_goal_has_only_its_share(world, monkeypatch):
    client, events, tasks, writer = world
    goal = _feature(client)
    assert _write(goal, tasks, events, writer)["number"] == 1
    _planned(client, events, tasks, goal, [draft("store")])
    moved = _goal(client, goal["id"])

    def asks(needs: str) -> None:
        with db.connect(autocommit=True) as conn:
            conn.execute("UPDATE planner.goal_update SET needs_you = %s::jsonb WHERE goal_id = %s", (needs, goal["id"]))

    asks("[]")
    assert _write(moved, tasks, events, writer) is None, "the last update is seconds old: the next pass writes it"

    # One that asked something of the operator does not wait: the goal moved, so what it asked may be done.
    asks('[{"what": "Approve the plan.", "ref": ""}]')
    assert _write(moved, tasks, events, writer)["number"] == 2

    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"max_per_goal": 3}})
    asked = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert asked.status_code == 200 and asked.json()["update"]["number"] == 3, "one asked for is written at once"
    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert refused.status_code == 502 and "updates.max_per_goal" in refused.json()["detail"]
    assert len(writer.calls) == 3


def test_an_update_that_cannot_be_written_says_so_with_what_to_do_and_keeps_the_last(world):
    client, events, tasks, writer = world
    goal = _feature(client)
    _write(goal, tasks, events, writer)
    writer.fail = "the HTTP Gateway answered 503: busy"

    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)

    assert refused.status_code == 502
    assert (
        refused.json()["detail"]
        == "the update could not be written: the HTTP Gateway answered 503: busy. Ask again in a moment"
    )
    assert [u["number"] for u in client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()["updates"]] == [
        1
    ]


def test_updates_turned_off_write_nothing_and_asking_says_where_to_turn_them_on(world, monkeypatch):
    client, events, tasks, writer = world
    goal = _feature(client)
    shipped = planning.settings()
    monkeypatch.setattr(planning, "settings", lambda: {**shipped, "updates": {"enabled": False}})

    assert _write(goal, tasks, events, writer) is None and writer.calls == []
    refused = client.post(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR)
    assert refused.status_code == 409 and "Configuration, Planning" in refused.json()["detail"]
    assert client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()["enabled"] is False


def test_a_pass_covers_open_goals_and_a_closed_one_only_for_its_last_word(world, at_once):
    client, events, tasks, writer = world
    quiet, told = _feature(client), _feature(client)
    _write(told, tasks, events, writer)
    for goal in (quiet, told):
        assert client.post(f"/v1/goals/{goal['id']}/cancel", headers=OPERATOR).status_code == 200

    listed = updates.goals()
    covered = {goal["id"] for goal in listed}
    # A goal as the pass reads it, straight from the database, makes facts a model call can carry.
    json.dumps([updates.facts(goal, [], [], None) for goal in listed])

    assert told["id"] in covered, "a goal the planner spoke of gets a last update when it closes"
    assert quiet["id"] not in covered
    last = _write(_goal(client, told["id"]), tasks, events, writer)
    assert last["number"] == 2 and writer.calls[-1][0]["goal"]["status"] == "canceled"


async def test_the_model_call_is_one_structured_request_on_the_configured_model(monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://gateway.test")
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    seen = []
    answer = {
        "headline": "Two of three tasks are done.",
        "body": "The API task merged.",
        "needs_you": [],
        "next": "The UI task starts.",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": answer, "finish_reason": "complete", "usd_cost": "0.0031"})

    written = await updates.write_update(
        _found(), {"headline": "h", "body": "b", "needs_you": [], "next": ""}, httpx.MockTransport(handler)
    )

    assert (written.headline, written.next, written.usd_cost) == (
        "Two of three tasks are done.",
        "The UI task starts.",
        "0.0031",
    )
    config = updates.settings()
    assert f"{seen[0]['engine']}:{seen[0]['model']}" == config["model"] == written.model
    assert seen[0]["max_response_tokens"] == config["max_response_tokens"]
    assert "--- your last update ---" in seen[0]["prompt"] and '"task": "running"' in seen[0]["prompt"]

    with pytest.raises(updates.UpdateError, match="answered 400"):
        await updates.write_update(_found(), None, httpx.MockTransport(lambda r: httpx.Response(400, text="no")))


def test_the_update_settings_are_checked_and_fall_back_to_goal_draftings_model(monkeypatch):
    shipped = planning.settings()
    assert kinds.check_planning(shipped, None) == []

    def reasons(**changed) -> list[str]:
        return kinds.check_planning({**shipped, "updates": {**shipped["updates"], **changed}}, None)

    assert reasons(enabled="no")[0].startswith("updates.enabled must be true or false")
    assert reasons(model="haiku")[0].startswith("updates.model must be <engine>:<model>")
    assert reasons(max_per_goal=0)[0].startswith("updates.max_per_goal must be a whole number above 0")
    assert reasons(min_seconds=1.5)[0].startswith("updates.min_seconds must be a whole number above 0")
    assert kinds.check_planning({k: v for k, v in shipped.items() if k != "updates"}, None) == []

    monkeypatch.setattr(planning, "settings", lambda: {k: v for k, v in shipped.items() if k != "updates"})
    from meta_harness.planner import drafting

    drafter = drafting.settings()
    assert (
        updates.settings()["model"] == f"{drafter['engine']}:{drafter['model']}"
        and updates.settings()["enabled"] is True
    )


def test_the_facts_say_where_each_task_stands_and_whether_it_waits_on_the_operator():
    link = {"state": "open", "held_reason": None, "refused_reason": None}
    reviewing = updates.stage(link, {"status": "running", "pr_url": "https://github.com/o/r/pull/9"}, False)

    assert "its own review" in reviewing and reviewing.endswith("Nothing is needed from the operator yet")
    assert updates.stage(link, {"status": "running"}, False).startswith("an agent is writing the change")
    assert updates.stage(link, {"status": "ready"}, False).endswith("It waits on the operator to merge it")
    assert updates.stage(link, {"status": "ready"}, True).endswith("Nothing is needed from the operator")
    assert (
        updates.stage(link, {"status": "running", "waiting_on": "an answer"}, False)
        == "waits on the operator: an answer"
    )
    assert updates.stage({**link, "held_reason": "large"}, None, False).endswith("It waits on the operator")
    assert updates.stage(link, None, False).startswith("not started yet")
    assert updates.stage({**link, "state": "closed"}, {"status": "closed"}, False) == "done: its issue is closed"
    assert "never for something a fact says needs nothing from them" in updates.SYSTEM_PROMPT


def test_every_need_gets_an_action_the_goals_records_support():
    links = [
        {"ref": "mh:i-1", "task_id": "t-1"},
        {"ref": "mh:i-2", "task_id": "t-2"},
        {"ref": "mh:i-3", "task_id": None},
    ]
    tasks = [
        {"id": "t-1", "status": "ready", "pr_url": "https://github.com/o/r/pull/1"},
        {"id": "t-2", "status": "running", "pr_url": None},
    ]
    active, planning = {"status": "active"}, {"status": "planning"}

    def one(goal: dict, **need) -> dict:
        [resolved] = updates.actionable([{"what": "Do it.", "ref": "", **need}], goal, links, tasks)
        return resolved

    merge = one(active, ref="mh:i-1", action="merge")
    assert (merge["action"], merge["task_id"], merge["pr_url"]) == ("merge", "t-1", "https://github.com/o/r/pull/1")
    assert one(active, ref="mh:i-2", action="merge")["action"] == "open_task", "no pull request to merge"
    assert one(active, ref="mh:i-3", action="open_task")["action"] == "open_issue", "no task yet"
    assert one(active, action="approve_plan")["action"] == "open_issue", "no plan waits on this goal"
    assert one(planning, action="")["action"] == "approve_plan"
    assert one({"status": "budget_exhausted"}, action="nonsense")["action"] == "raise_budget"
    assert one(active, ref="mh:i-1", action="review")["action"] == "merge", "a pull request ready to merge is merged"
    assert all(n["action"] in updates.ACTIONS for n in [merge])


def test_a_tasks_or_issues_own_state_picks_the_buttons_whatever_the_model_wrote():
    links = [
        {"ref": "mh:i-1", "task_id": "t-1"},
        {"ref": "mh:i-2", "task_id": "t-2"},
        {"ref": "mh:i-3", "task_id": None, "held_reason": "it is large: 4 areas"},
        {
            "ref": "mh:i-4",
            "task_id": None,
            "held_reason": "the plan split it into 3 smaller tasks, which run in its place",
        },
        {"ref": "mh:i-5", "task_id": "t-5"},
    ]
    tasks = [
        {"id": "t-1", "status": "review_unresolved", "pr_url": "https://github.com/o/r/pull/1"},
        {"id": "t-2", "status": "failed", "pr_url": None},
        {"id": "t-5", "status": "ready", "pr_url": "https://github.com/o/r/pull/5"},
    ]

    def action(ref: str, wanted: str) -> str:
        [need] = updates.actionable(
            [{"what": "Look.", "ref": ref, "action": wanted}], {"status": "active"}, links, tasks
        )
        return need["action"]

    # A review no fix settled is settled on the spot, not just opened.
    assert action("mh:i-1", "review") == "settle_review"
    assert action("mh:i-2", "open_task") == "retry"
    assert action("mh:i-3", "open_issue") == "split_or_run"
    assert action("mh:i-4", "open_issue") == "open_issue", "already split: nothing left to choose"
    assert action("mh:i-5", "review") == "merge"


def test_what_was_unstuck_since_the_last_update_is_a_fact_told_not_asked():
    tasks = [{"id": "t-1", "name": "Skill import", "title": "Import a skill", "status": "queued", "pr_url": None}]
    links = [{"ref": "mh:i-1", "task_id": "t-1", "state": "open", "title": "Import a skill"}]
    unstuck = [
        {
            "task_id": "t-1",
            "ref": None,
            "because": "it failed on its own account",
            "did": "ran it again on the complex tier, with a stronger model",
            "outcome": "started",
            "next": "if it fails again, I will ask you",
        },
        {
            "task_id": None,
            "ref": "jira:VL-9",
            "because": "too large",
            "did": "split it into 3 smaller issues",
            "outcome": "done",
            "next": "they run in its place",
        },
    ]
    found = updates.facts({"status": "active"}, links, tasks, None, None, unstuck)
    assert [(u["task"], u["did"]) for u in found["unstuck"]] == [
        ("Skill import", "ran it again on the complex tier, with a stronger model"),
        ("jira:VL-9", "split it into 3 smaller issues"),
    ]
    assert updates.digest(found) != updates.digest(updates.facts({"status": "active"}, links, tasks, None)), (
        "something unstuck is news: the next update says it"
    )
    assert "unstick" in updates.SYSTEM_PROMPT and "not something to do" in updates.SYSTEM_PROMPT


def test_an_update_may_say_a_goal_is_done_only_when_none_of_its_issues_is_open():
    done = updates.Written(headline="All tasks merged, goal done.", body="The goal is complete.")
    left = updates.Written(headline="One task left.", body="The last one failed and waits on you.")

    updates.written_true(done, {"goal": {"open_issues": 0}})
    updates.written_true(left, {"goal": {"open_issues": 1}})
    with pytest.raises(updates.UpdateError, match="1 of its issues is open"):
        updates.written_true(done, {"goal": {"open_issues": 1}})
    for claim in ("The goal is complete.", "All seven replacement tasks are now in.", "All issues are done."):
        with pytest.raises(updates.UpdateError):
            updates.written_true(updates.Written(headline="News", body=claim), {"goal": {"open_issues": 2}})


def test_an_update_that_wrongly_says_done_is_written_again_once_and_never_posted_as_it_was(world, at_once, monkeypatch):
    client, events, tasks, _ = world
    goal = _feature(client)
    real = updates.facts

    def one_open(*args, **more):
        found = real(*args, **more)
        found["goal"]["open_issues"] = 1
        return found

    monkeypatch.setattr(updates, "facts", one_open)
    seen = []

    async def corrects(found, before):
        seen.append(found)
        if "correction" in found:
            return updates.Written(headline="One task left.", body="It failed and waits on you.", model="m")
        return updates.Written(headline="All tasks merged, goal done.", body="The goal is complete.", model="m")

    saved = _write(goal, tasks, events, corrects)

    assert saved["headline"] == "One task left." and len(seen) == 2
    assert "said the goal was done" in seen[1]["correction"] or "is not" in seen[1]["correction"]

    async def insists(found, before):
        return updates.Written(headline="All tasks merged, goal done.", body="The goal is complete.", model="m")

    # Twice wrong: the update is written from the facts, never shown as an error.
    plain = asyncio.run(updates.write(goal, tasks, events, insists, asked=True))
    assert plain["headline"] == "1 issue still open."


def test_an_update_from_the_facts_names_each_open_issue_and_where_it_stands():
    found = {
        "goal": {"open_issues": 1},
        "issues": [
            {
                "ref": "jira:ABC-1",
                "title": "Describe the change",
                "state": "open",
                "replaced_by": None,
                "where_it_stands": "its pull request is open, and Octofusion is running its checks",
            },
            {
                "ref": "jira:ABC-2",
                "title": "Done part",
                "state": "closed",
                "replaced_by": None,
                "where_it_stands": "done: its issue is closed",
            },
        ],
    }

    plain = updates.from_facts(found, "claude:m", "0.01")

    assert plain.headline == "1 issue still open."
    assert plain.body == "Describe the change: its pull request is open, and Octofusion is running its checks."
    assert (plain.model, plain.usd_cost, plain.needs_you) == ("claude:m", "0.01", [])
    updates.written_true(plain, found)


def test_a_plan_that_cannot_be_approved_is_never_offered_for_approval():
    """A sentence asking to split two tasks carried an Approve button."""
    stuck = {"version": 1, "invalid": ["the task api is too large for one pull request"]}
    need = {"what": "Decide whether to split the two large tasks or draft a new plan.", "ref": ""}

    def actions(goal: dict, plan: dict | None, action: str) -> list[str]:
        found = updates.actionable([{**need, "action": action}], goal, [], [], None, plan)
        return [n["action"] for n in found]

    waiting = {"status": "planning", "plan_task_id": None}
    assert actions(waiting, stuck, "approve_plan") == ["fix_plan"]
    assert actions(waiting, stuck, "open_issue") == ["fix_plan"], "about no issue, so about the plan"
    assert actions(waiting, {"version": 2, "invalid": []}, "approve_plan") == ["approve_plan"]
    # The planner is resizing the plan itself: the version the need speaks of is being replaced.
    resizing = {"status": "planning", "plan_task_id": "t-9"}
    assert actions(resizing, stuck, "approve_plan") == []
    assert actions(resizing, stuck, "raise_budget") == ["raise_budget"], "a need about something else stays"


def test_the_facts_say_when_the_planner_is_writing_a_new_plan_version_itself():
    plan = {"version": 1, "invalid": ["the task api is too large"], "drafts": [], "summary": "", "gaps": []}
    goal = {"id": "g-1", "status": "planning", "planned": True, "plan_task_id": "t-9"}

    assert updates.facts(goal, [], [{"id": "t-9", "status": "running"}], plan)["plan"]["replanning"] is True
    assert updates.facts({**goal, "plan_task_id": None}, [], [], plan)["plan"]["replanning"] is False
    assert updates.facts(goal, [], [], None)["plan"]["replanning"] is False, "the first plan is drafted, not replaced"
    assert "ask the operator nothing about the plan" in updates.SYSTEM_PROMPT


def test_a_new_plan_version_brings_a_new_update_and_the_page_is_told_where_the_plan_stands(world, at_once):
    """The panel described version 1 after version 2 had replaced it."""
    client, events, tasks, writer = world
    goal = _feature(client)
    _planned(client, events, tasks, goal, [draft("store"), draft("api", ("store",))])
    first = _write(_goal(client, goal["id"]), tasks, events, writer)

    edited = client.put(f"/v1/goals/{goal['id']}/plan", json={"drafts": [draft("store")]}, headers=OPERATOR)
    assert edited.status_code == 200, edited.text
    shown = client.get(f"/v1/goals/{goal['id']}/updates", headers=OPERATOR).json()
    assert (shown["updates"][0]["plan_version"], shown["plan"]) == (1, {"version": 2, "replanning": False}), (
        "until the next update is written, the page can tell this one is about a plan since replaced"
    )

    second = _write(_goal(client, goal["id"]), tasks, events, writer)

    assert (first["plan_version"], second["plan_version"]) == (1, 2), "a new version is news by itself"
    assert client.get(f"/v1/goals/{goal['id']}", headers=OPERATOR).json()["status"] == "planning"
