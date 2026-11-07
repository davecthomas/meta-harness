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
