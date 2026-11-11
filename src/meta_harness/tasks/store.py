"""The task service's Postgres store and the task state machine."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from meta_harness.money import usd

TASK_TYPES = {"bug", "feature", "refactor", "dependency", "test", "security", "docs", "plan", "review", "fix", "smoke"}
# A deploy's own check that tasks still run (smoke.py): only a service creates one, and the lists a person
# reads leave them out unless asked.
SERVICE_TASK_TYPES = {"smoke"}
# Only the meta-harness creates these.
INTERNAL_TASK_TYPES = {"review", "fix"}
# These may name no acceptance check: a planning task's result is its issue drafts, and a smoke task passes by
# ending closed. Every other task says what shows it is done.
CHECKLESS_TASK_TYPES = {"plan", "smoke"}
COMPLEXITIES = {"low", "medium", "high"}

# Each status and the statuses it may move to. closed is the end. failed and canceled end the task's workflow,
# and so do ready and review_unresolved; an operator may send any of those four back to queued (retry, merge).
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "blocked", "canceled"},
    "blocked": {"queued", "canceled"},
    "running": {"queued", "taken_over", "ready", "review_unresolved", "closed", "failed", "canceled"},
    "taken_over": {"running", "closed", "canceled"},
    "ready": {"closed", "canceled", "queued"},
    "review_unresolved": {"closed", "canceled", "queued"},
    "closed": set(),
    "failed": {"queued"},
    "canceled": {"queued"},
}
# Statuses a task does not leave by itself, and a cancel has nothing to stop in.
TERMINAL = {"closed", "failed", "canceled"}
# The statuses an operator may retry a task from, and may ask for its pull request's merge from.
RETRYABLE = ("review_unresolved", "failed", "canceled")
MERGEABLE = ("ready", "review_unresolved")
COLUMNS = (
    "id, caller, external_ref, repo, task_type, parent_task_id, goal_id, depends_on, priority, title, description, "
    "acceptance_checks, paths, complexity, max_tier, max_usd, pin, status, status_reason, cancel_requested, owner, "
    "pr_url, head_sha, created_at, updated_at, attempts, waiting_on, retries, resume, review_claimed_by, "
    "status_changed_at, recoveries, spent_carried_usd, drives"
)
# The most times the supervisor sends one parked task back by itself, for its merge or through its review
# loop, between an operator's retries. Past it the task waits for an operator.
MAX_DRIVES = 3
# The most times the supervisor sends one task back to the queue after finding no workflow behind it. Past
# it the task fails, for an operator: something ends its workflows, and another try would end the same way.
MAX_RECOVERIES = 3


class TaskError(ValueError):
    """A request the task service refuses. The message says why."""


class NotFound(LookupError):
    pass


@dataclass
class NewTask:
    external_ref: str
    repo: str
    task_type: str
    priority: int
    title: str
    description: str
    acceptance_checks: list[str]
    parent_task_id: str | None = None
    goal_id: str | None = None
    depends_on: list[str] | None = None
    paths: list[str] | None = None
    complexity: str | None = None
    max_tier: str | None = None
    max_usd: Decimal | None = None
    pin: str | None = None


def new_task_id() -> str:
    return f"t-{uuid.uuid4().hex[:12]}"


def check(task: NewTask, *, internal: bool) -> None:
    if task.task_type not in TASK_TYPES:
        raise TaskError(f"task_type {task.task_type!r} is not one of {sorted(TASK_TYPES)}")
    if task.task_type in (INTERNAL_TASK_TYPES | SERVICE_TASK_TYPES) and not internal:
        raise TaskError(f"only the meta-harness creates {task.task_type} tasks")
    if task.task_type in INTERNAL_TASK_TYPES and not task.parent_task_id:
        raise TaskError(f"a {task.task_type} task needs parent_task_id")
    if task.complexity is not None and task.complexity not in COMPLEXITIES:
        raise TaskError(f"complexity {task.complexity!r} is not one of {sorted(COMPLEXITIES)}")
    if not task.acceptance_checks and task.task_type not in CHECKLESS_TASK_TYPES:
        raise TaskError(f"a {task.task_type} task needs at least one acceptance check")


def create(conn: psycopg.Connection, caller: str, task: NewTask, *, internal: bool = False) -> tuple[dict, bool]:
    """Create a task, or return the caller's existing one with that external_ref. Returns (task, created)."""
    check(task, internal=internal)
    status = "blocked" if task.depends_on else "queued"
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"INSERT INTO tasks.task (id, caller, external_ref, repo, task_type, parent_task_id, goal_id, depends_on, "
            f"priority, title, description, acceptance_checks, paths, complexity, max_tier, max_usd, pin, status) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"ON CONFLICT (caller, external_ref) DO NOTHING RETURNING {COLUMNS}",
            (
                new_task_id(),
                caller,
                task.external_ref,
                task.repo,
                task.task_type,
                task.parent_task_id,
                task.goal_id,
                task.depends_on or [],
                task.priority,
                task.title,
                task.description,
                Jsonb(task.acceptance_checks),
                task.paths or [],
                task.complexity,
                task.max_tier,
                task.max_usd,
                task.pin,
                status,
            ),
        )
        row = cur.fetchone()
        if row is not None:
            return _jsonable(row), True
        cur.execute(
            f"SELECT {COLUMNS} FROM tasks.task WHERE caller = %s AND external_ref = %s", (caller, task.external_ref)
        )
        return _jsonable(cur.fetchone()), False


def get(conn: psycopg.Connection, task_id: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE id = %s", (task_id,))
        row = cur.fetchone()
    if row is None:
        raise NotFound(task_id)
    return _jsonable(row)


def find(conn: psycopg.Connection, caller: str | None, external_ref: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        if caller is None:
            cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE external_ref = %s", (external_ref,))
        else:
            cur.execute(
                f"SELECT {COLUMNS} FROM tasks.task WHERE caller = %s AND external_ref = %s", (caller, external_ref)
            )
        row = cur.fetchone()
    if row is None:
        raise NotFound(external_ref)
    return _jsonable(row)


def list_tasks(
    conn: psycopg.Connection,
    caller: str | None,
    statuses: list[str] | None,
    goal_id: str | None,
    limit: int,
    offset: int = 0,
    pr: bool | None = None,
    q: str | None = None,
    waiting: bool | None = None,
    smoke: bool = False,
) -> tuple[list[dict], int]:
    """One page of tasks, highest priority then oldest first, and how many match in all.

    pr True: only tasks with a pull request; False: only tasks without one; None: either.
    q: text in the title, the id or the issue ref, any case.
    waiting True: only tasks waiting on an operator (waiting_on set); False: only those that are not.
    smoke: also list deploys' smoke tasks, which a list for a person leaves out.
    """
    clauses, params = [], []
    if not smoke:
        clauses.append("task_type <> 'smoke'")
    if waiting is not None:
        clauses.append("waiting_on IS NOT NULL" if waiting else "waiting_on IS NULL")
    if caller is not None:
        clauses.append("caller = %s")
        params.append(caller)
    if statuses:
        clauses.append("status = ANY(%s)")
        params.append(statuses)
    if goal_id:
        clauses.append("goal_id = %s")
        params.append(goal_id)
    if pr is not None:
        clauses.append("pr_url IS NOT NULL" if pr else "pr_url IS NULL")
    if q:
        clauses.append("(title ILIKE %s OR id ILIKE %s OR external_ref ILIKE %s)")
        params += [f"%{q}%"] * 3
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM tasks.task {where} ORDER BY priority DESC, created_at, id LIMIT %s OFFSET %s",
            [*params, limit, offset],
        )
        rows = [_jsonable(row) for row in cur.fetchall()]
        cur.execute(f"SELECT count(*) AS n FROM tasks.task {where}", params)
        return rows, cur.fetchone()["n"]


def set_status(
    conn: psycopg.Connection, task_id: str, status: str, reason: str | None = None, **fields: Any
) -> tuple[dict, str]:
    """Move a task to a new status, with any of pr_url, head_sha, owner and waiting_on ("" clears it; a task
    that leaves running stops waiting). Returns (task, previous status)."""
    if status not in TRANSITIONS:
        raise TaskError(f"unknown status {status!r}")
    allowed = {"pr_url", "head_sha", "owner", "waiting_on"}
    if set(fields) - allowed:
        raise TaskError(f"set_status takes only {sorted(allowed)}")
    if fields.get("waiting_on") and fields["waiting_on"] not in WAITING_ON:
        raise TaskError(f"waiting_on is one of {', '.join(WAITING_ON)}")
    if "waiting_on" in fields:
        fields["waiting_on"] = fields["waiting_on"] or None
    elif status != "running":
        fields["waiting_on"] = None
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        previous = row["status"]
        if status != previous and status not in TRANSITIONS[previous]:
            raise TaskError(f"a {previous} task cannot become {status}")
        sets = ["status = %s", "status_reason = %s", "updated_at = now()"]
        if status != previous:
            sets.append("status_changed_at = now()")
        params: list[Any] = [status, reason]
        for name, value in fields.items():
            sets.append(f"{name} = %s")
            params.append(value)
        cur.execute(f"UPDATE tasks.task SET {', '.join(sets)} WHERE id = %s RETURNING {COLUMNS}", [*params, task_id])
        return _jsonable(cur.fetchone()), previous


def request_cancel(conn: psycopg.Connection, task_id: str, reason: str) -> tuple[dict, str]:
    """Cancel a task that has not started, or flag a running one for the meta-harness run service to stop."""
    task = get(conn, task_id)
    if task["status"] in TERMINAL:
        raise TaskError(f"the task is already {task['status']}")
    if task["status"] in {"queued", "blocked", "ready", "review_unresolved"}:
        return set_status(conn, task_id, "canceled", reason)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"UPDATE tasks.task SET cancel_requested = true, updated_at = now() WHERE id = %s RETURNING {COLUMNS}",
            (task_id,),
        )
        return _jsonable(cur.fetchone()), task["status"]


def _jsonable(row: dict) -> dict:
    for key in ("created_at", "updated_at"):
        row[key] = row[key].isoformat()
    if row.get("status_changed_at") is not None:
        row["status_changed_at"] = row["status_changed_at"].isoformat()
    if row["max_usd"] is not None:
        row["max_usd"] = str(row["max_usd"])
    if "spent_carried_usd" in row:
        row["spent_carried_usd"] = str(row["spent_carried_usd"])
    return row


# How an operator message reaches a running task: at its next turn, when it would stop, or stop it after this turn.
# budget is the operator's answer to a task held for budget: its own endpoint queues it, not the messages one.
# review is an operator's word on a review that waits for them: a finding dismissed, or go on to the fix now.
MESSAGE_KINDS = ("next_turn", "after_stop", "stop", "choose", "budget", "review")
# What a running task may be waiting on an operator for.
WAITING_ON = ("budget", "review", "operator_review")
# The most attempts a task makes at once.
MAX_ATTEMPTS = 4


def set_attempts(conn: psycopg.Connection, task_id: str, count: int) -> dict:
    """Ask a task that has not started to make `count` attempts at once. Refused once it runs."""
    if not 1 <= count <= MAX_ATTEMPTS:
        raise TaskError(f"attempts is 1 to {MAX_ATTEMPTS}")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"UPDATE tasks.task SET attempts = %s, updated_at = now() WHERE id = %s AND status IN ('queued', 'blocked') "
            f"RETURNING {COLUMNS}",
            (count, task_id),
        )
        row = cur.fetchone()
    if row is None:
        get(conn, task_id)
        raise TaskError("only a task that has not started can make several attempts")
    return _jsonable(row)


def add_message(conn: psycopg.Connection, task_id: str, body: str, deliver: str, actor: str) -> dict:
    """Queue an operator's message for a running task. Refused unless the task is running."""
    if deliver not in MESSAGE_KINDS:
        raise TaskError(f"when is one of {', '.join(MESSAGE_KINDS)}")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM tasks.task WHERE id = %s FOR SHARE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] != "running":
            raise TaskError(f"a {row['status']} task has no run to message")
        cur.execute(
            "INSERT INTO tasks.task_message (task_id, body, deliver, actor) VALUES (%s, %s, %s, %s) "
            "RETURNING id, task_id, body, deliver, actor",
            (task_id, body, deliver, actor),
        )
        return dict(cur.fetchone())


def requeue(
    conn: psycopg.Connection, task_id: str, action: str, max_usd: Decimal | None, actor: str
) -> tuple[dict, str]:
    """Sends a task whose workflow ended back to the queue, for a new RunTask workflow.

    action retry: from review_unresolved, failed or canceled. A task with a pull request resumes at its review
    loop; one without starts with a new first run. action merge: from ready or review_unresolved, with a pull
    request; the workflow merges it and waits for the merge commit's checks. max_usd, when given, is the task's
    new spend limit; either way the new workflow's runs start from nothing spent.
    """
    allowed = RETRYABLE if action == "retry" else MERGEABLE
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status, pr_url FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] not in allowed:
            raise TaskError(f"a {row['status']} task cannot be {'retried' if action == 'retry' else 'merged'}")
        if action == "merge" and not row["pr_url"]:
            raise TaskError("the task has no pull request to merge")
        resume = "merge" if action == "merge" else ("review" if row["pr_url"] else None)
        what = "asked for its pull request's merge" if action == "merge" else "retried it"
        cur.execute(
            "UPDATE tasks.task SET status = 'queued', status_reason = %s, resume = %s, retries = retries + 1, "
            "cancel_requested = false, waiting_on = NULL, max_usd = coalesce(%s, max_usd), updated_at = now(), "
            "status_changed_at = now(), spent_carried_usd = 0, drives = 0 "
            f"WHERE id = %s RETURNING {COLUMNS}",
            (f"{actor} {what}", resume, max_usd, task_id),
        )
        return _jsonable(cur.fetchone()), row["status"]


def drive(conn: psycopg.Connection, task_id: str, action: str, why: str, spent_usd: Decimal) -> tuple[dict, str]:
    """Sends a parked task back to the queue for the supervisor: a `ready` or `review_unresolved` task whose
    workflow ended with its pull request open, which the supervisor found it can move on.

    action merge: its next workflow merges the pull request, and resolves a conflict with its base first.
    action review: its pull request goes back through checks, review and fixes. action retry: a `failed`
    task whose failure was the system's runs again. Any way the workflow
    counts spent_usd, what the task's runs spent so far, against its spend limit: unlike an operator's retry,
    a drive never gives a task its limit again. Refused once the task has been driven MAX_DRIVES times.
    Returns (task, previous status).
    """
    if action not in ("merge", "review", "retry"):
        raise TaskError(f"a drive is merge, review or retry, not {action!r}")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status, pr_url, drives FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if action == "retry":
            if row["status"] != "failed":
                raise TaskError(f"a {row['status']} task has not failed")
        elif row["status"] not in MERGEABLE:
            raise TaskError(f"a {row['status']} task is not waiting with a pull request open")
        elif not row["pr_url"]:
            raise TaskError("the task has no pull request")
        if row["drives"] >= MAX_DRIVES:
            raise TaskError(f"the supervisor has already sent the task back {row['drives']} times")
        what = {
            "merge": "asked for its merge",
            "review": "sent it back through its review loop",
            "retry": "retried it",
        }[action]
        # A retried task resumes at its pull request when it has one, and starts over when it has none.
        resume = action if action != "retry" else ("review" if row["pr_url"] else None)
        cur.execute(
            "UPDATE tasks.task SET status = 'queued', status_reason = %s, resume = %s, retries = retries + 1, "
            "drives = drives + 1, cancel_requested = false, waiting_on = NULL, spent_carried_usd = %s, "
            f"updated_at = now(), status_changed_at = now() WHERE id = %s RETURNING {COLUMNS}",
            (f"{why}, so the supervisor {what} (try {row['drives'] + 1} of {MAX_DRIVES})", resume, spent_usd, task_id),
        )
        return _jsonable(cur.fetchone()), row["status"]


def recover(conn: psycopg.Connection, task_id: str, why: str, spent_usd: Decimal) -> tuple[dict, str]:
    """Ends the wait of a task that has no RunTask workflow behind it, which nothing else would end.

    The supervisor calls it for a `running` task, or a `queued` one the scheduler started, whose workflow
    ended or never existed (why says which). A task asked to cancel is canceled: nothing is left to stop.
    Otherwise it goes back to the queue for a new workflow, which resumes at its pull request when it has
    one and counts spent_usd, what its runs spent so far, against its spend limit. After MAX_RECOVERIES it
    fails instead, for an operator. Returns (task, previous status).
    """
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status, pr_url, cancel_requested, recoveries FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] not in ("running", "queued"):
            raise TaskError(f"a {row['status']} task has no workflow to recover")
        if row["cancel_requested"]:
            status, reason, resume = "canceled", f"it was asked to cancel, and {why}", None
        elif row["recoveries"] >= MAX_RECOVERIES:
            status, resume = "failed", None
            reason = (
                f"{why}, for the {row['recoveries'] + 1}th time: its workflows keep ending without finishing, "
                "so it was not started again"
            )
        else:
            status, resume = "queued", ("review" if row["pr_url"] else None)
            reason = f"{why}, so the supervisor sent it back to the queue (recovery {row['recoveries'] + 1})"
        recovered = 1 if status == "queued" else 0
        cur.execute(
            "UPDATE tasks.task SET status = %s, status_reason = %s, resume = %s, waiting_on = NULL, "
            "retries = retries + %s, recoveries = recoveries + %s, spent_carried_usd = %s, "
            "cancel_requested = false, updated_at = now(), status_changed_at = now() "
            f"WHERE id = %s RETURNING {COLUMNS}",
            (status, reason, resume, recovered, recovered, spent_usd, task_id),
        )
        return _jsonable(cur.fetchone()), row["status"]


def review_word(conn: psycopg.Connection, task_id: str, body: str, actor: str) -> dict:
    """Queues an operator's word on the review a task holds for them (a dismissal, or go on now) for its
    workflow. Refused unless the task is running and waiting on its review. Returns the task."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE id = %s FOR SHARE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] != "running" or row["waiting_on"] != "review":
            raise TaskError("the task is not holding a review for an operator")
        cur.execute(
            "INSERT INTO tasks.task_message (task_id, body, deliver, actor) VALUES (%s, %s, 'review', %s)",
            (task_id, body, actor),
        )
        return _jsonable(row)


def claim_review(conn: psycopg.Connection, task_id: str, actor: str) -> dict:
    """An operator takes the review of a running task's pull request over: the round in flight, or the next,
    waits for their verdict. The first claim wins. Queues the claim for the task's workflow. Returns the task."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] != "running" or not row["pr_url"]:
            raise TaskError("only a running task with a pull request has a review to take over")
        if row["review_claimed_by"] and row["review_claimed_by"] != actor:
            raise TaskError(f"{row['review_claimed_by']} already holds this review")
        if row["review_claimed_by"] == actor:
            return _jsonable(row)
        cur.execute(
            f"UPDATE tasks.task SET review_claimed_by = %s, updated_at = now() WHERE id = %s RETURNING {COLUMNS}",
            (actor, task_id),
        )
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO tasks.task_message (task_id, body, deliver, actor) VALUES (%s, %s, 'review', %s)",
            (task_id, f"claim:{actor}", actor),
        )
        return _jsonable(row)


def end_review_claim(conn: psycopg.Connection, task_id: str, actor: str, word: str) -> dict:
    """The operator who holds a task's review gives it back (word "release") or gives their verdict (word
    "verdict:<json>", once the round waits for it). Queues the word for the workflow. Returns the task."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if not row["review_claimed_by"]:
            raise TaskError("no one holds this task's review")
        if row["review_claimed_by"] != actor:
            raise TaskError(f"{row['review_claimed_by']} holds this review, not {actor}")
        if word.startswith("verdict:") and (row["status"] != "running" or row["waiting_on"] != "operator_review"):
            raise TaskError("the agent's review has not finished: the round is not waiting for a verdict yet")
        cur.execute(
            f"UPDATE tasks.task SET review_claimed_by = NULL, updated_at = now() WHERE id = %s RETURNING {COLUMNS}",
            (task_id,),
        )
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO tasks.task_message (task_id, body, deliver, actor) VALUES (%s, %s, 'review', %s)",
            (task_id, word, actor),
        )
        return _jsonable(row)


def decide_budget(conn: psycopg.Connection, task_id: str, action: str, max_usd: Decimal | None, actor: str) -> dict:
    """An operator's answer to a task held for budget: raise (with a new spend limit for the task, or none when
    its goal's budget was raised) or end. Queues it for the task's workflow. Returns the task."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status, waiting_on, max_usd FROM tasks.task WHERE id = %s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(task_id)
        if row["status"] != "running" or row["waiting_on"] != "budget":
            raise TaskError("the task is not held for budget")
        body = action
        if action == "raise" and max_usd is not None:
            if row["max_usd"] is not None and max_usd <= row["max_usd"]:
                raise TaskError(f"the new limit must be above the task's {usd(row['max_usd'])}")
            cur.execute("UPDATE tasks.task SET max_usd = %s, updated_at = now() WHERE id = %s", (max_usd, task_id))
            body = f"raise:{max_usd}"
        cur.execute(
            "INSERT INTO tasks.task_message (task_id, body, deliver, actor) VALUES (%s, %s, 'budget', %s)",
            (task_id, body, actor),
        )
        cur.execute(f"SELECT {COLUMNS} FROM tasks.task WHERE id = %s", (task_id,))
        return _jsonable(cur.fetchone())


def pending_messages(conn: psycopg.Connection, limit: int = 100) -> list[dict]:
    """Messages not yet handed to their task's workflow, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, task_id, body, deliver FROM tasks.task_message WHERE delivered_at IS NULL ORDER BY id LIMIT %s",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def mark_delivered(conn: psycopg.Connection, message_id: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE tasks.task_message SET delivered_at = now() WHERE id = %s", (message_id,))
