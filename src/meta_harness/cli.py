"""`mh`, the meta-harness command line."""

import json
import time

import click

from meta_harness import __version__, db, migrations


@click.group()
@click.version_option(__version__, prog_name="mh")
def main() -> None:
    """The meta-harness command line."""


@main.command()
def migrate() -> None:
    """Apply every component's pending schema migrations."""
    with db.connect() as conn:
        applied = migrations.apply(conn)
    if not applied:
        click.echo("migrate: nothing to apply")
    for migration in applied:
        click.echo(f"migrate: applied {migration.component}/{migration.name}")


@main.group()
def events() -> None:
    """The event service: serve it, append to it, read and tail it."""


@events.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8301, show_default=True)
def events_serve(host: str, port: int) -> None:
    """Run the event service."""
    import uvicorn

    from meta_harness.events.service import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@events.command("emit")
@click.option("--count", default=10, show_default=True, help="How many debug_ping events to send.")
@click.option("--every", default=0.0, show_default=True, help="Seconds between events.")
@click.option("--run", "run_id", default=None, help="The run_id to put on each event.")
def events_emit(count: int, every: float, run_id: str | None) -> None:
    """Send debug_ping events through the client library's outbox, as a test producer."""
    from meta_harness.events.client import EventClient

    client = EventClient(source="mh-cli")
    for n in range(1, count + 1):
        client.emit("debug_ping", {"n": n}, run_id=run_id)
        if every:
            time.sleep(every)
    if not client.flush(timeout=60):
        raise click.ClickException("the outbox did not empty within 60 seconds")
    client.close()
    click.echo(f"emit: sent {count} debug_ping events, rejected {client.rejected}, dropped {client.dropped}")


def _filters(run_id, task_id, event_type, source) -> dict[str, str]:
    given = {"run_id": run_id, "task_id": task_id, "event_type": event_type, "source": source}
    return {k: v for k, v in given.items() if v is not None}


def _line(event: dict) -> str:
    who = event.get("run_id") or event.get("task_id") or "-"
    return f"{event['position']:>8}  {event['time_utc'][:23]}  {event['event_type']:<24} {who:<28} {json.dumps(event['payload'])}"


@events.command("tail")
@click.option("--run", "run_id", default=None)
@click.option("--task", "task_id", default=None)
@click.option("--type", "event_type", default=None)
@click.option("--source", default=None)
@click.option("--from", "from_position", default=0, show_default=True, help="Start after this position.")
def events_tail(run_id, task_id, event_type, source, from_position) -> None:
    """Print events as they commit, reconnecting across event service restarts. Ctrl-C stops."""
    from meta_harness.events.client import EventClient

    client = EventClient()
    try:
        for event in client.tail(from_position, **_filters(run_id, task_id, event_type, source)):
            click.echo(_line(event))
    except KeyboardInterrupt:
        pass


@main.group()
def tasks() -> None:
    """The task service."""


@tasks.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8302, show_default=True)
def tasks_serve(host: str, port: int) -> None:
    """Run the task service and its Task API. Keys come from MH_TASK_API_KEYS and MH_SERVICE_KEYS."""
    import uvicorn

    from meta_harness.events.client import EventClient
    from meta_harness.tasks.service import create_app

    uvicorn.run(create_app(EventClient(source="task")), host=host, port=port, log_level="warning")


@main.group()
def task() -> None:
    """Submit and inspect tasks through the Task API (MH_TASKS_URL, MH_TASK_API_KEY)."""


def _task_line(t: dict) -> str:
    return f"{t['id']}  {t['status']:<17} {t['task_type']:<9} {t['repo']:<36} {t['external_ref']}  {t['title'][:50]}"


def _client():
    from meta_harness.tasks.client import TaskClient

    return TaskClient()


def _task_call(fn, *args, **kwargs):
    from meta_harness.tasks.client import TaskApiError

    try:
        return fn(*args, **kwargs)
    except TaskApiError as err:
        raise click.ClickException(str(err)) from err


@task.command("create")
@click.option("--ref", "external_ref", required=True, help="Your own id for the task, unique per caller.")
@click.option("--repo", required=True)
@click.option("--title", required=True)
@click.option("--description", default="", show_default=True)
@click.option("--check", "checks", multiple=True, required=True, help="An acceptance check; repeat for more.")
@click.option("--type", "task_type", default="feature", show_default=True)
@click.option("--priority", default=0, show_default=True)
@click.option("--depends-on", multiple=True)
@click.option("--max-usd", default=None)
@click.option("--pin", default=None)
@click.option("--path", "paths", multiple=True, help="A path the task changes; tasks on one area run in turn.")
def task_create(external_ref, repo, title, description, checks, task_type, priority, depends_on, max_usd, pin, paths):
    """Submit a task. Submitting the same --ref again returns the existing task."""
    body = {
        "external_ref": external_ref,
        "repo": repo,
        "title": title,
        "description": description or title,
        "acceptance_checks": list(checks),
        "task_type": task_type,
        "priority": priority,
        "depends_on": list(depends_on) or None,
        "max_usd": max_usd,
        "pin": pin,
        "paths": list(paths) or None,
    }
    click.echo(_task_line(_task_call(_client().create, **body)))


@task.command("get")
@click.argument("task_id_or_ref")
def task_get(task_id_or_ref: str) -> None:
    """One task, by id (t-...) or by your external_ref."""
    client = _client()
    fn = client.get if task_id_or_ref.startswith("t-") else client.find
    click.echo(json.dumps(_task_call(fn, task_id_or_ref), indent=2))


@task.command("list")
@click.option("--status", default=None, help="Comma-separated statuses.")
@click.option("--limit", default=50, show_default=True)
def task_list(status: str | None, limit: int) -> None:
    """Tasks, highest priority first."""
    for t in _task_call(_client().list, status.split(",") if status else None, None, limit):
        click.echo(_task_line(t))


@task.command("cancel")
@click.argument("task_id")
@click.option("--reason", default="canceled from the mh command line", show_default=True)
def task_cancel(task_id: str, reason: str) -> None:
    """Cancel a task, or ask the meta-harness run service to stop a running one."""
    t = _task_call(_client().cancel, task_id, reason)
    click.echo(_task_line(t) + ("  (cancel requested)" if t["cancel_requested"] and t["status"] != "canceled" else ""))


@main.group()
def planner() -> None:
    """The planner: serve it, or sweep goals' issues into tasks now."""


def _planner_parts():
    import os

    from meta_harness.events.client import EventClient
    from meta_harness.planner.tracker import GitHubTracker
    from meta_harness.tasks.client import TaskClient

    return EventClient(source="planner"), GitHubTracker(), TaskClient(api_key=os.environ.get("MH_SERVICE_KEY"))


@planner.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8303, show_default=True)
def planner_serve(host: str, port: int) -> None:
    """Run the planner service. It sweeps every active goal every MH_PLANNER_SWEEP_SECONDS."""
    import uvicorn

    from meta_harness.planner.service import create_app

    events_client, tracker, tasks_client = _planner_parts()
    uvicorn.run(create_app(events_client, tracker, tasks_client), host=host, port=port, log_level="warning")


@planner.command("sweep")
def planner_sweep() -> None:
    """Sweep every active goal once and print what each took in."""
    import asyncio

    from meta_harness.planner.sweep import sweep_active

    events_client, tracker, tasks_client = _planner_parts()
    for result in asyncio.run(sweep_active(tracker, tasks_client, events_client)):
        click.echo(
            f"{result.goal_id}: joined {result.joined or '-'}, refused {result.refused or '-'}, "
            f"closed {result.closed or '-'}, unblocked {result.unblocked or '-'}"
            + (f", errors {result.errors}" if result.errors else "")
        )
    events_client.close()


@main.group()
def goal() -> None:
    """Create and steer goals through the planner (MH_PLANNER_URL, MH_OPERATOR_KEY)."""


def _goals():
    from meta_harness.planner.client import GoalsClient

    return GoalsClient()


def _goal_line(g: dict) -> str:
    return f"{g['id']}  {g['status']:<16} {g['modality']:<4} label {g['label']:<24} {', '.join(g['repos'])}"


@goal.command("create")
@click.option("--statement", required=True)
@click.option("--repo", "repos", multiple=True, required=True, help="A registered repo; repeat for more.")
@click.option("--label", required=True, help="The issue label that queues an issue for this goal.")
@click.option("--auto-merge/--no-auto-merge", default=False, show_default=True)
@click.option("--max-usd", default=None)
@click.option("--priority", default=0, show_default=True)
def goal_create(statement, repos, label, auto_merge, max_usd, priority) -> None:
    """Create a fix goal. The planner takes in its labeled issues right away."""
    body = {
        "statement": statement,
        "repos": list(repos),
        "label": label,
        "auto_merge": auto_merge,
        "max_usd": max_usd,
        "priority": priority,
    }
    click.echo(_goal_line(_task_call(_goals().create, **body)))


@goal.command("list")
def goal_list() -> None:
    for g in _task_call(_goals().list):
        click.echo(_goal_line(g))


@goal.command("get")
@click.argument("goal_id")
def goal_get(goal_id: str) -> None:
    """A goal and the issues it took in."""
    click.echo(json.dumps(_task_call(_goals().get, goal_id), indent=2))


@goal.command("pause")
@click.argument("goal_id")
def goal_pause(goal_id: str) -> None:
    click.echo(_goal_line(_task_call(_goals().action, goal_id, "pause")))


@goal.command("resume")
@click.argument("goal_id")
def goal_resume(goal_id: str) -> None:
    click.echo(_goal_line(_task_call(_goals().action, goal_id, "resume")))


@goal.command("cancel")
@click.argument("goal_id")
def goal_cancel(goal_id: str) -> None:
    click.echo(_goal_line(_task_call(_goals().action, goal_id, "cancel")))


@goal.command("sweep")
@click.argument("goal_id")
def goal_sweep(goal_id: str) -> None:
    """Take in the goal's labeled issues now."""
    click.echo(json.dumps(_task_call(_goals().action, goal_id, "sweep"), indent=2))


@main.group()
def edge() -> None:
    """The local edge: the console web app and the REST API on one origin, as the cloud serves them."""


@edge.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8300, show_default=True)
def edge_serve(host: str, port: int) -> None:
    """Serve console/dist and forward /v1 to the owning services as the signed-in operator.

    MH_CONSOLE_DEV_IDENTITY is who is signed in; MH_CONSOLE_OPERATORS is the access list.
    """
    import uvicorn

    from meta_harness.edge.app import create_app

    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@main.group()
def scheduler() -> None:
    """The meta-harness scheduler: admits queued tasks and starts their RunTask workflows."""


def _admission():
    import os

    from meta_harness.events.client import EventClient
    from meta_harness.planner.client import GoalsClient
    from meta_harness.scheduler.admission import Admission
    from meta_harness.scheduler.service import cancel_run_task, signal_run_task, start_run_task
    from meta_harness.tasks.client import TaskClient

    from meta_harness.config import store
    from meta_harness.config.kinds import ConfigError

    # A task policy the scheduler would refuse stops it here, with the setting's name.
    try:
        store.max_parallel()
    except ConfigError as err:
        raise click.ClickException(str(err)) from err
    service_key = os.environ.get("MH_SERVICE_KEY")
    return Admission(
        TaskClient(api_key=service_key),
        GoalsClient(api_key=service_key),
        EventClient(source="scheduler"),
        start_run_task,
        max_running=store.max_parallel,
        cancel=cancel_run_task,
        signal=signal_run_task,
    )


@scheduler.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8304, show_default=True)
def scheduler_serve(host: str, port: int) -> None:
    """Run the meta-harness scheduler: an admission pass every MH_ADMISSION_SECONDS."""
    import logging

    import uvicorn

    from meta_harness.scheduler.service import create_app

    logging.basicConfig(level=logging.INFO, format="[scheduler] %(message)s")
    uvicorn.run(create_app(_admission()), host=host, port=port, log_level="warning")


@scheduler.command("admit")
def scheduler_admit() -> None:
    """Run one admission pass and print what it started and held."""
    import asyncio

    admission = _admission()
    result = asyncio.run(admission.pass_once())
    for task_id in result.started:
        click.echo(f"started {task_id}")
    for task_id, reason in result.held.items():
        click.echo(f"held {task_id}: {reason}")
    for task_id in result.released:
        click.echo(f"released {task_id}")
    admission.events.close()


@main.group()
def run() -> None:
    """The meta-harness run service: its worker, and starting a task's RunTask workflow."""


@run.command("worker")
def run_worker() -> None:
    """Run the meta-harness run service's Temporal worker."""
    import asyncio

    from meta_harness.run_service import worker

    asyncio.run(worker.main())


@run.command("start")
@click.argument("task_id")
@click.option("--wait", is_flag=True, help="Wait for the RunTask workflow to end, then print the task.")
def run_start(task_id: str, wait: bool) -> None:
    """Start the RunTask workflow for a task the task service holds."""
    import asyncio

    from meta_harness.run_service import start

    try:
        workflow_id = asyncio.run(start.start(task_id))
        click.echo(f"run: started RunTask workflow {workflow_id}")
    except start.AlreadyStarted as err:
        if not wait:
            raise click.ClickException(str(err)) from err
        click.echo(f"run: {err}, so this waits for it")
    if not wait:
        return
    result, outcome = asyncio.run(start.wait(task_id))
    if result is None:
        click.echo(f"run: the RunTask workflow did not complete: {outcome}")
    else:
        for a in result.attempts:
            click.echo(
                f"run: run {a.attempt} on {a.candidate} (tier {a.tier or 'pinned'}): {a.decision}, ${a.spent_usd}"
            )
        click.echo(f"run: spent ${result.spent_usd}")
    from meta_harness.tasks.client import TaskClient

    import os

    t = _task_call(TaskClient(api_key=os.environ.get("MH_SERVICE_KEY")).get, task_id)
    click.echo(f"run: task {task_id} is {t['status']}" + (f", pull request {t['pr_url']}" if t["pr_url"] else ""))
    if result is None:
        raise SystemExit(1)


@events.command("read")
@click.option("--run", "run_id", default=None)
@click.option("--task", "task_id", default=None)
@click.option("--type", "event_type", default=None)
@click.option("--source", default=None)
@click.option("--from", "from_position", default=0, show_default=True)
@click.option("--limit", default=500, show_default=True)
def events_read(run_id, task_id, event_type, source, from_position, limit) -> None:
    """Print stored events after a position."""
    from meta_harness.events.client import EventClient

    page = EventClient().read(from_position, limit, **_filters(run_id, task_id, event_type, source))
    for event in page["events"]:
        click.echo(_line(event))


if __name__ == "__main__":
    main()
