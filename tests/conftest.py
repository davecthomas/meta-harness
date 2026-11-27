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

from meta_harness import db, retrying


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


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Retries run in tests, against each test's fake service, without their backoff."""

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(retrying, "_sleep", lambda seconds: None)
    monkeypatch.setattr(retrying, "_async_sleep", no_wait)
    # Nor the pace sizing calls keep between them.
    from meta_harness.planner import sizing

    monkeypatch.setattr(sizing, "_sleep", no_wait)
    monkeypatch.setattr(sizing, "_pace", sizing._Pace())


@pytest.fixture(autouse=True)
def goals_run_unplanned(monkeypatch):
    """Tests start from goals that take their issues in as they come: most are about what happens to a goal's
    issues and tasks, not about planning. The shipped configuration plans every goal first; the planning tests
    turn that on themselves (tests/planner/test_planning.py's `planned`), and one test reads the shipped file."""
    from meta_harness.planner import planning

    monkeypatch.setattr(planning, "plans_every_goal", lambda: False)


@pytest.fixture
def unchecked_text(monkeypatch):
    """Lets a test of an adapter's own behavior hand it plain strings: the adapters otherwise take only text
    that came through the outbound pipeline (outbound.require), which tests/outbound proves on its own."""
    from meta_harness import outbound

    monkeypatch.setattr(outbound, "require", lambda text, what, destination, title=False: None)


@pytest.fixture(autouse=True)
def local_repos_under_tmp(monkeypatch, tmp_path_factory):
    """The bare repos tests make sit under pytest's temporary directory, so that is the one place a task may
    name a local repo (meta_harness.local_repos)."""
    monkeypatch.setenv("MH_LOCAL_REPO_ROOTS", str(tmp_path_factory.getbasetemp()))


def _allowed_hosts() -> set[str]:
    """Where a test may connect: this machine, and the test database's host."""
    return {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", urlsplit(TEST_DATABASE_URL).hostname or ""}


@pytest.fixture(autouse=True)
def no_outside_network(monkeypatch):
    """No test reaches a host outside this machine and its database: the name does not resolve, at once.

    A test that left a client unfaked used to pass or fail by the network it ran on. Where a made-up host
    was refused at once the test passed; in a task's sandbox the lookup hung, a wait in the code under test
    ran out, and three overview tests failed there on every run while passing everywhere else.
    """
    import ipaddress
    import socket

    allowed = _allowed_hosts()
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def local(host: object) -> bool:
        name = host.decode() if isinstance(host, bytes) else str(host or "")
        if name in allowed:
            return True
        try:
            return ipaddress.ip_address(name).is_loopback
        except ValueError:
            return False

    def getaddrinfo(host, *args, **kwargs):
        if not local(host):
            raise socket.gaierror(socket.EAI_NONAME, f"tests reach no outside host: {host!r} was asked for")
        return real_getaddrinfo(host, *args, **kwargs)

    resolved: set[str] = set()

    def remembering(host, *args, **kwargs):
        found = getaddrinfo(host, *args, **kwargs)
        resolved.update(str(info[4][0]) for info in found)
        return found

    def connect(self, address):
        # A Unix socket's address is a path; an allowed name's addresses were resolved through getaddrinfo.
        if isinstance(address, tuple) and not local(address[0]) and str(address[0]) not in resolved:
            raise ConnectionRefusedError(f"tests reach no outside host: {address[0]!r} was asked for")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", remembering)
    monkeypatch.setattr(socket.socket, "connect", connect)
