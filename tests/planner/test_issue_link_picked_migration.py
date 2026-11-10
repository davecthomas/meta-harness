"""The planner's migrations on goals that existed before them."""

import shutil
import uuid
from datetime import UTC, datetime

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from meta_harness import db, migrations
from meta_harness.events import store as event_store

pytestmark = pytest.mark.db
PLANNER = migrations.PACKAGE_DIR / "planner" / "migrations"


@pytest.fixture
def conn():
    try:
        connection = db.connect()
        migrations.apply(connection)
    except Exception as err:  # noqa: BLE001
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")
    event_store.sync_registry(connection)
    yield connection
    connection.close()


def _planner_up_to(package, component, last: int):
    """Copy the planner's migrations up to and including `last` into the component's migrations."""
    directory = package / component / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    for path in sorted(PLANNER.glob("*.sql")):
        if int(path.name[:4]) <= last:
            shutil.copy(path, directory / path.name)


def _event(conn, event_type, ref, goal_id):
    conn.execute(
        "INSERT INTO events.event (event_id, event_type, schema_version, time_utc, source, payload) "
        "VALUES (%s, %s, 1, %s, 'planner', %s)",
        (uuid.uuid4(), event_type, datetime.now(UTC), Jsonb({"ref": ref, "goal_id": goal_id})),
    )


def test_picked_keeps_the_refs_an_operator_added_to_an_existing_labeled_goal(conn, tmp_path):
    component = f"mhtest_{uuid.uuid4().hex[:8]}"
    schema = sql.Identifier(component)
    run = uuid.uuid4().hex[:8]
    labeled, labelless, other = f"g-{run}-1", f"g-{run}-2", f"g-{run}-3"
    added, swept, re_swept, elsewhere, unlabeled = (f"github:o/r#{run}{n}" for n in range(1, 6))
    try:
        _planner_up_to(tmp_path, component, 6)
        migrations.apply(conn, tmp_path)
        for goal_id, label in ((labeled, "mh:x"), (labelless, None), (other, "mh:y")):
            conn.execute(
                sql.SQL(
                    "INSERT INTO {}.goal (id, created_by, statement, repos, label, modality, modality_source, status) "
                    "VALUES (%s, 'op', 'A.', '{{o/r}}', %s, 'issue_backlog', 'operator', 'active')"
                ).format(schema),
                (goal_id, label),
            )
        for ref, goal_id in (
            (added, labeled),
            (swept, labeled),
            (re_swept, labeled),
            (elsewhere, labeled),
            (unlabeled, labelless),
        ):
            conn.execute(
                sql.SQL("INSERT INTO {}.issue_link (ref, goal_id, repo, state) VALUES (%s, %s, 'o/r', 'open')").format(
                    schema
                ),
                (ref, goal_id),
            )
        _event(conn, "issue_added", added, labeled)
        # Picked, dropped, then swept in again by the goal's label.
        _event(conn, "issue_added", re_swept, labeled)
        _event(conn, "issue_dropped", re_swept, labeled)
        # Picked for another goal before this one's label swept it in.
        _event(conn, "issue_added", elsewhere, other)

        _planner_up_to(tmp_path, component, 7)
        applied = migrations.apply(conn, tmp_path)

        assert [m.name for m in applied] == ["0007_issue_link_picked.sql"]
        rows = conn.execute(sql.SQL("SELECT ref, picked FROM {}.issue_link").format(schema)).fetchall()
        assert dict(rows) == {added: True, swept: False, re_swept: False, elsewhere: False, unlabeled: True}
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(schema))
        conn.execute("DELETE FROM events.event WHERE payload ->> 'goal_id' = ANY(%s)", ([labeled, labelless, other],))
