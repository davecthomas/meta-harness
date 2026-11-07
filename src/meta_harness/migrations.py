"""Schema migrations, one set per owning component.

Each component keeps its migrations in `src/meta_harness/<component>/migrations/`,
as SQL files named `NNNN_<what>.sql`, and owns the Postgres schema of the same
name. A migration runs once, in file order, in its own transaction, and is
recorded in `<schema>.schema_migrations`. An advisory lock keeps two `mh migrate`
runs from applying the same file.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

PACKAGE_DIR = Path(__file__).parent
MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
# Any constant works; it names the lock `mh migrate` holds while it runs.
ADVISORY_LOCK_ID = 7_200_001


@dataclass(frozen=True)
class Migration:
    component: str
    version: int
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


def discover(package_dir: Path = PACKAGE_DIR) -> dict[str, list[Migration]]:
    """Every component's migrations, in file order. A misnamed or repeated version stops the run."""
    found: dict[str, list[Migration]] = {}
    for directory in sorted(package_dir.glob("*/migrations")):
        component = directory.parent.name
        migrations = []
        for path in sorted(directory.glob("*.sql")):
            match = MIGRATION_NAME.match(path.name)
            if not match:
                raise ValueError(f"{path}: a migration is named NNNN_<what>.sql")
            migrations.append(Migration(component, int(match.group(1)), path))
        versions = [m.version for m in migrations]
        if len(versions) != len(set(versions)):
            raise ValueError(f"{directory}: two migrations share a version number")
        if migrations:
            found[component] = migrations
    return found


def apply(conn: psycopg.Connection, package_dir: Path = PACKAGE_DIR) -> list[Migration]:
    """Apply every migration not yet applied. Returns the ones it applied."""
    applied: list[Migration] = []
    conn.autocommit = True
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
    try:
        for component, migrations in discover(package_dir).items():
            schema = sql.Identifier(component)
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
            conn.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {}.schema_migrations ("
                    "version integer PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
                ).format(schema)
            )
            done = {row[0] for row in conn.execute(sql.SQL("SELECT version FROM {}.schema_migrations").format(schema))}
            for migration in migrations:
                if migration.version in done:
                    continue
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
                    conn.execute(migration.path.read_text())
                    conn.execute(
                        sql.SQL("INSERT INTO {}.schema_migrations (version, name) VALUES (%s, %s)").format(schema),
                        (migration.version, migration.name),
                    )
                applied.append(migration)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
    return applied
