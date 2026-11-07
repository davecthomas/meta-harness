"""Postgres connections for the control-plane components.

Every component owns one schema. In the cloud each connects as its own Cloud SQL
role; locally they share one database and one user.
"""

import os

import psycopg
from psycopg_pool import ConnectionPool

DEFAULT_DATABASE_URL = "postgresql://mh:mh@127.0.0.1:5444/mh"


def database_url() -> str:
    """The database the control plane uses: MH_DATABASE_URL, or the local `make up` Postgres."""
    return os.environ.get("MH_DATABASE_URL", DEFAULT_DATABASE_URL)


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """A new connection. The caller closes it."""
    return psycopg.connect(url or database_url(), autocommit=autocommit)


def pool(url: str | None = None, max_size: int = 10) -> ConnectionPool:
    """A service's connection pool. Connections autocommit, so every `conn.transaction()` is a real
    transaction that commits when its block ends, never a savepoint inside an implicit one."""
    return ConnectionPool(url or database_url(), min_size=1, max_size=max_size, open=True, kwargs={"autocommit": True})
