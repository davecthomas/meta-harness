"""Tests use their own database, mh_test, on the same Postgres as MH_DATABASE_URL.

Test tasks, goals and locks never land in the development database, where a
running meta-harness scheduler would admit them. MH_TEST_DATABASE_URL names
another test database.
"""

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from meta_harness import db


def _test_database_url() -> str:
    explicit = os.environ.get("MH_TEST_DATABASE_URL")
    if explicit:
        return explicit
    parts = urlsplit(db.database_url())
    return urlunsplit(parts._replace(path="/mh_test"))


def _ensure_database(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    admin = urlunsplit(urlsplit(url)._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


TEST_DATABASE_URL = _test_database_url()
try:
    _ensure_database(TEST_DATABASE_URL)
except Exception:  # noqa: BLE001 - no Postgres: the database tests skip
    pass
os.environ["MH_DATABASE_URL"] = TEST_DATABASE_URL


@pytest.fixture
def conn():
    """A connection to the test database. Tests that use it skip when Postgres is not up."""
    try:
        connection = db.connect(autocommit=True)
    except Exception as err:  # noqa: BLE001 - any failure to connect means no database for this run
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")
    with connection:
        yield connection
