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
def task_create(external_ref, repo, title, description, checks, task_type, priority, depends_on, max_usd, pin):
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
