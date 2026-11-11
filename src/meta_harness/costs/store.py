"""The cost service's Postgres store: daily costs, and how far each source has been harvested."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

# The dimensions a cost row has, as the API names them, and the column each is.
DIMENSIONS = {
    "source": "source",
    "kind": "item",
    "goal": "goal_id",
    "task": "task_id",
    "repo": "repo",
    "model": "model",
    "engine": "engine",
    "operator": "operator",
}
KEY = "day, source, item, goal_id, task_id, repo, model, engine, operator"


@dataclass(frozen=True)
class Cost:
    day: date
    source: str
    item: str
    usd: Decimal
    goal_id: str = ""
    task_id: str = ""
    repo: str = ""
    model: str = ""
    engine: str = ""
    operator: str = ""

    def values(self) -> tuple:
        return (
            self.day,
            self.source,
            self.item,
            self.goal_id,
            self.task_id,
            self.repo,
            self.model,
            self.engine,
            self.operator,
            self.usd,
        )


def position(conn: psycopg.Connection, source: str) -> int:
    row = conn.execute("SELECT position FROM costs.harvest WHERE source = %s", (source,)).fetchone()
    return row[0] if row else 0


def add(conn: psycopg.Connection, source: str, costs: list[Cost], upto: int) -> None:
    """Add costs harvested from events up to a position, and move the source's position there, in one
    transaction: an event's cost is counted once, however a harvest ends."""
    with conn.transaction():
        for cost in costs:
            conn.execute(
                f"INSERT INTO costs.daily_cost ({KEY}, usd) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"ON CONFLICT ({KEY}) DO UPDATE SET usd = costs.daily_cost.usd + EXCLUDED.usd",
                cost.values(),
            )
        _harvested(conn, source, upto, None)


def replace(conn: psycopg.Connection, source: str, first: date, last: date, costs: list[Cost]) -> None:
    """Put a source's costs for a span of days in place of what was there: a bill is restated for days."""
    with conn.transaction():
        conn.execute("DELETE FROM costs.daily_cost WHERE source = %s AND day BETWEEN %s AND %s", (source, first, last))
        for cost in costs:
            conn.execute(
                f"INSERT INTO costs.daily_cost ({KEY}, usd) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"ON CONFLICT ({KEY}) DO UPDATE SET usd = costs.daily_cost.usd + EXCLUDED.usd",
                cost.values(),
            )
        _harvested(conn, source, 0, None)


def failed(conn: psycopg.Connection, source: str, error: str) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO costs.harvest (source, error) VALUES (%s, %s) "
            "ON CONFLICT (source) DO UPDATE SET error = EXCLUDED.error",
            (source, error[:500]),
        )


def _harvested(conn: psycopg.Connection, source: str, upto: int, error: str | None) -> None:
    conn.execute(
        "INSERT INTO costs.harvest (source, position, error) VALUES (%s, %s, %s) ON CONFLICT (source) DO UPDATE "
        "SET position = GREATEST(costs.harvest.position, EXCLUDED.position), harvested_at = now(), error = NULL",
        (source, upto, error),
    )


def harvests(conn: psycopg.Connection) -> dict[str, dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT source, harvested_at, error FROM costs.harvest")
        return {
            row["source"]: {"harvested_at": row["harvested_at"].isoformat(), "error": row["error"]}
            for row in cur.fetchall()
        }


def _where(first: date, last: date, filters: dict[str, list[str]]) -> tuple[str, list]:
    clauses, params = ["day BETWEEN %s AND %s"], [first, last]
    for name, values in filters.items():
        clauses.append(f"{DIMENSIONS[name]} = ANY(%s)")
        params.append(values)
    return " AND ".join(clauses), params


def by_day(
    conn: psycopg.Connection, first: date, last: date, group_by: str, filters: dict[str, list[str]]
) -> list[tuple[date, str, Decimal]]:
    """Each day's cost for each value of a dimension, within the filters."""
    where, params = _where(first, last, filters)
    column = DIMENSIONS[group_by]
    return conn.execute(
        f"SELECT day, {column}, sum(usd) FROM costs.daily_cost WHERE {where} GROUP BY day, {column} ORDER BY day",
        params,
    ).fetchall()


def total(conn: psycopg.Connection, first: date, last: date, filters: dict[str, list[str]]) -> Decimal:
    where, params = _where(first, last, filters)
    return conn.execute(f"SELECT coalesce(sum(usd), 0) FROM costs.daily_cost WHERE {where}", params).fetchone()[0]


def top(
    conn: psycopg.Connection, first: date, last: date, dimension: str, filters: dict[str, list[str]], limit: int
) -> list[tuple[str, Decimal]]:
    """The values of a dimension that cost the most, dearest first. A row with no value for it is left out."""
    where, params = _where(first, last, filters)
    column = DIMENSIONS[dimension]
    return conn.execute(
        f"SELECT {column}, sum(usd) AS usd FROM costs.daily_cost WHERE {where} AND {column} <> '' "
        f"GROUP BY {column} ORDER BY usd DESC LIMIT %s",
        [*params, limit],
    ).fetchall()


def values(conn: psycopg.Connection, dimension: str, filters: dict[str, list[str]]) -> list[str]:
    """Every value a dimension takes, for a filter's choices, within the scope the filters give."""
    column = DIMENSIONS[dimension]
    scope = {k: v for k, v in filters.items() if k in ("source", "operator")}
    clauses, params = ["true"], []
    for name, wanted in scope.items():
        clauses.append(f"{DIMENSIONS[name]} = ANY(%s)")
        params.append(wanted)
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM costs.daily_cost WHERE {' AND '.join(clauses)} AND {column} <> '' "
        f"ORDER BY {column}",
        params,
    ).fetchall()
    return [row[0] for row in rows]
