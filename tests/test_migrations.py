import uuid

import pytest
from psycopg import sql

from meta_harness import migrations


def _component(tmp_path, files: dict[str, str]):
    """A package dir holding one component with these migration files. Returns (package_dir, component)."""
    component = f"mhtest_{uuid.uuid4().hex[:8]}"
    directory = tmp_path / component / "migrations"
    directory.mkdir(parents=True)
    for name, body in files.items():
        (directory / name).write_text(body)
    return tmp_path, component


def test_discover_orders_by_file_name(tmp_path):
    package, component = _component(tmp_path, {"0002_b.sql": "", "0001_a.sql": ""})
    found = migrations.discover(package)
    assert [m.name for m in found[component]] == ["0001_a.sql", "0002_b.sql"]


def test_discover_refuses_a_misnamed_file(tmp_path):
    package, _ = _component(tmp_path, {"1_a.sql": ""})
    with pytest.raises(ValueError, match="NNNN_"):
        migrations.discover(package)


def test_discover_refuses_a_repeated_version(tmp_path):
    package, _ = _component(tmp_path, {"0001_a.sql": "", "0001_b.sql": ""})
    with pytest.raises(ValueError, match="share a version"):
        migrations.discover(package)


def test_the_shipped_migrations_are_well_formed():
    migrations.discover()


@pytest.mark.db
def test_apply_runs_each_migration_once_in_its_own_schema(conn, tmp_path):
    package, component = _component(
        tmp_path,
        {
            "0001_create.sql": "CREATE TABLE thing (id integer PRIMARY KEY);",
            "0002_insert.sql": "INSERT INTO thing VALUES (1);",
        },
    )
    try:
        first = migrations.apply(conn, package)
        second = migrations.apply(conn, package)
        assert [m.name for m in first] == ["0001_create.sql", "0002_insert.sql"]
        assert second == []
        rows = conn.execute(sql.SQL("SELECT id FROM {}.thing").format(sql.Identifier(component))).fetchall()
        assert rows == [(1,)]
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(component)))


@pytest.mark.db
def test_a_failed_migration_leaves_nothing_behind(conn, tmp_path):
    package, component = _component(tmp_path, {"0001_bad.sql": "CREATE TABLE ok (id integer); SELECT nope;"})
    try:
        with pytest.raises(Exception):
            migrations.apply(conn, package)
        recorded = conn.execute(
            sql.SQL("SELECT count(*) FROM {}.schema_migrations").format(sql.Identifier(component))
        ).fetchone()
        assert recorded == (0,)
        exists = conn.execute("SELECT to_regclass(%s)", (f"{component}.ok",)).fetchone()
        assert exists == (None,)
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(component)))
