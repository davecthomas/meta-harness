import pytest

from meta_harness import db


@pytest.fixture
def conn():
    """A connection to MH_DATABASE_URL. Tests that use it skip when Postgres is not up."""
    try:
        connection = db.connect(autocommit=True)
    except Exception as err:  # noqa: BLE001 - any failure to connect means no database for this run
        pytest.skip(f"no Postgres at {db.database_url()}: {err}")
    with connection:
        yield connection
