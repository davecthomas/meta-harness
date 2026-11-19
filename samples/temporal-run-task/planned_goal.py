"""The planned-goal step of the zero-cost end-to-end check (e2e.sh runs it through stack.sh).

    python planned_goal.py ORIGIN

It drives one goal the whole way through the planner, as an operator's console would: a goal with no
issue tracker is saved over the local bare repo ORIGIN, the planner's planning task drafts its plan
(the fake HTTP Gateway scripts that run), the plan is approved with its new tasks kept in the
meta-harness, the scheduler runs each task to a pushed branch, and the planner writes its update on
the goal. It needs the planner, task service, event service and scheduler up, the planner's repo
registry naming ORIGIN (MH_REPOS_CONFIG), and a service key (MH_SERVICE_KEY). Exits 0 when all of
that happened, and 1 saying what did not.
"""

import os
import subprocess
import sys
import time
from typing import Any

import httpx

from meta_harness import retrying

PLANNER = os.environ.get("MH_PLANNER_URL", "http://127.0.0.1:8303").rstrip("/")
PLAN_SECONDS = 300
TASKS_SECONDS = 600
UPDATE_SECONDS = 120


def log(text: str) -> None:
    print(f"[planned-goal] {text}", flush=True)


def stop(text: str) -> None:
    log(text)
    sys.exit(1)


def wait(what: str, seconds: int, done: Any) -> Any:
    """Polls done() each second until it returns something, for up to seconds. Stops the check when it never does."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if found := done():
            return found
        time.sleep(1)
    stop(f"{what} did not happen within {seconds} seconds")


def main(origin: str) -> None:
    headers = {"Authorization": f"Bearer {os.environ.get('MH_SERVICE_KEY', '')}", "X-MH-Actor": "e2e"}
    http = httpx.Client(base_url=PLANNER, timeout=60, headers=headers, transport=retrying.transport())

    def get(path: str) -> dict:
        response = http.get(path)
        if response.status_code != 200:
            stop(f"GET {path} answered {response.status_code}: {response.text[:300]}")
        return response.json()

    registered = [repo["name"] for repo in get("/v1/repos")["repos"]]
    if origin not in registered:
        stop(
            f"the planner's repo registry lacks {origin}. The check starts the planner with its own registry: "
            "stop services you started yourself (make stop at the repo root), then run make e2e again"
        )

    body = {"statement": "Greet whoever opens the repo.", "repos": [origin], "modality": "feature", "tracker": "mh"}
    created = http.post("/v1/goals", json=body)
    if created.status_code != 201:
        stop(f"the goal was not created: {created.status_code} {created.text[:300]}")
    goal = created.json()
    goal_id = goal["id"]
    if goal["status"] != "planning":
        stop(f"goal {goal_id} is {goal['status']}: a new goal is planned before any of its tasks run")
    log(f"goal {goal_id} is planning, with no issue tracker")

    def planned() -> dict | None:
        shown = get(f"/v1/goals/{goal_id}/plan")
        plan = shown.get("plan")
        if plan and plan.get("invalid"):
            stop(f"the plan cannot be approved: {plan['invalid']}")
        return plan if plan and plan.get("drafts") else None

    plan = wait("the planning task's plan", PLAN_SECONDS, planned)
    if len(plan["drafts"]) != 2 or not plan.get("summary"):
        stop(f"expected a plan of two new tasks with a summary, got {plan}")
    log(f"plan version {plan['version']}: {[d['draft_id'] for d in plan['drafts']]}, and the planner's summary")

    approved = http.post(f"/v1/goals/{goal_id}/approve", json={"track": False})
    if approved.status_code != 200:
        stop(f"the plan was not approved: {approved.status_code} {approved.text[:300]}")
    log("the plan is approved, its new tasks kept in the meta-harness")

    def ready() -> list[dict] | None:
        issues = get(f"/v1/goals/{goal_id}/issues")["issues"]
        ended = [i for i in issues if i.get("task_status") in ("failed", "canceled")]
        if ended:
            stop(f"a task of the goal ended {ended[0]['task_status']}: {ended[0]}")
        return issues if len(issues) == 2 and all(i.get("task_status") == "ready" for i in issues) else None

    issues = wait("both of the goal's tasks becoming ready", TASKS_SECONDS, ready)
    for issue in issues:
        if not issue["ref"].startswith("mh:"):
            stop(f"{issue['ref']} is not a task kept in the meta-harness")
        branch = f"mh/{issue['task_id']}:hello.txt"
        if subprocess.run(["git", "--git-dir", origin, "cat-file", "-e", branch], check=False).returncode:
            stop(f"expected branch mh/{issue['task_id']} with hello.txt in {origin}")
    log(f"tasks {[i['task_id'] for i in issues]} are ready, each with its branch pushed")

    def told() -> dict | None:
        updates = get(f"/v1/goals/{goal_id}/updates")
        if updates.get("error"):
            log(f"the planner's update is being tried again: {updates['error']}")
        return next((u for u in updates["updates"] if u["headline"] and u["body"]), None)

    update = wait("the planner's update on the goal", UPDATE_SECONDS, told)
    log(f"the planner's update {update['number']} is on the goal's page")
    log(f"goal {goal_id} was planned, approved and run, with no issue tracker and no model call")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        stop("usage: python planned_goal.py ORIGIN")
    main(sys.argv[1])
