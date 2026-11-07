"""`mh`, the meta-harness command line."""

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
