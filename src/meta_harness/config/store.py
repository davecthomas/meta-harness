"""Configuration versions in Postgres: save, read the active one, list, and roll back.

A kind with no versions is seeded from the deployment's file the first time
anything reads it, so a fresh deployment starts from what it shipped with.
"""

import json
import os
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from meta_harness import db
from meta_harness.config.kinds import KINDS, ConfigError

SOURCE_ENV = "MH_CONFIG_SOURCE"


def from_database() -> bool:
    """True when this deployment's services read configuration from here rather than from files."""
    return os.environ.get(SOURCE_ENV, "file") == "db"


def _row(row: tuple | None) -> dict[str, Any] | None:
    if row is None:
        return None
    kind, version, document, edited_by, note, origin, rolled_back_from, created_at = row
    return {
        "kind": kind,
        "version": version,
        "document": document,
        "edited_by": edited_by,
        "note": note,
        "origin": origin,
        "rolled_back_from": rolled_back_from,
        "created_at": created_at.isoformat(),
    }


COLUMNS = "v.kind, v.version, v.document, v.edited_by, v.note, v.origin, v.rolled_back_from, v.created_at"


def active(conn: psycopg.Connection, kind: str) -> dict[str, Any]:
    """The active version of a kind, seeding it from the deployment's file when it has none."""
    _known(kind)
    row = conn.execute(
        f"SELECT {COLUMNS} FROM config.active a JOIN config.config_version v USING (kind, version) WHERE a.kind = %s",
        (kind,),
    ).fetchone()
    if row is None:
        return _seed(conn, kind)
    return _row(row)


def active_version(conn: psycopg.Connection, kind: str) -> int | None:
    row = conn.execute("SELECT version FROM config.active WHERE kind = %s", (kind,)).fetchone()
    return row[0] if row else None


def versions(conn: psycopg.Connection, kind: str, limit: int = 50) -> list[dict[str, Any]]:
    _known(kind)
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM config.config_version v WHERE v.kind = %s ORDER BY v.version DESC LIMIT %s",
        (kind, limit),
    ).fetchall()
    return [_row(r) for r in rows]


def others(conn: psycopg.Connection, kind: str) -> dict[str, dict[str, Any]]:
    """The other kinds' active documents, which a document is checked against."""
    return {name: active(conn, name)["document"] for name in KINDS if name != kind}


def check(conn: psycopg.Connection, kind: str, document: dict[str, Any]) -> None:
    reasons = KINDS[kind].check(document, others(conn, kind))
    if reasons:
        raise ConfigError(reasons)


def save(conn: psycopg.Connection, kind: str, document: dict[str, Any], who: str, note: str | None) -> dict[str, Any]:
    """Checks a document and makes it the kind's active version. Raises ConfigError with every reason."""
    # Seed first, so the deployment's shipped version is always version 1 and can be rolled back to.
    active(conn, kind)
    with conn.transaction():
        check(conn, kind, document)
        return _insert(conn, kind, document, who, note, "edit", None)


def rollback(conn: psycopg.Connection, kind: str, version: int, who: str, note: str | None) -> dict[str, Any]:
    """Makes an earlier version's document active again, as a new version, after checking it still fits."""
    _known(kind)
    with conn.transaction():
        row = conn.execute(
            "SELECT document FROM config.config_version WHERE kind = %s AND version = %s", (kind, version)
        ).fetchone()
        if row is None:
            raise LookupError(f"{kind} has no version {version}")
        check(conn, kind, row[0])
        return _insert(conn, kind, row[0], who, note or f"rolled back to version {version}", "rollback", version)


# The kinds whose entries, by name, reach the deployment from the shipped files: the document's key
# holding them. Tiers are left out: they are an ordered routing list, and an added tier changes routing.
SHIPPED_ENTRIES = {"profiles": "profiles"}
SHIPPED_BY = "meta-harness"


def add_shipped(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Adds to each kind in SHIPPED_ENTRIES the entries its shipped file has that no version ever had.

    So a profile added in code reaches a deployment that reads its configuration from here. An entry
    whose name is in any earlier version is never added again: an operator who changed or removed it
    meant to. Each addition is a new version, origin `shipped`, checked like any save. A kind whose
    additions would be refused is left as it is. Returns the versions saved.
    """
    saved = []
    for kind, key in SHIPPED_ENTRIES.items():
        current = active(conn, kind)
        shipped = KINDS[kind].seed().get(key) or {}
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"config:{kind}",))
            seen = {
                name
                for (name,) in conn.execute(
                    "SELECT DISTINCT jsonb_object_keys(document->%s) FROM config.config_version WHERE kind = %s",
                    (key, kind),
                ).fetchall()
            }
            added = {name: entry for name, entry in shipped.items() if name not in seen}
            if not added:
                continue
            document = {**current["document"], key: {**current["document"].get(key, {}), **added}}
            try:
                check(conn, kind, document)
            except ConfigError:
                continue
            note = f"added from the shipped file: {', '.join(sorted(added))}"
            saved.append(_insert(conn, kind, document, SHIPPED_BY, note, "shipped", None))
    return saved


def replace_engines(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Moves the active tiers' candidates off engines that cannot run a harness (REPLACED_ENGINES).

    Every run on such a candidate fails on its first model call, so no operator meant to keep one. The
    change is a new version, origin `shipped`, checked like any save, and nothing else in the document
    moves. Returns the versions saved: none when the tiers name no replaced engine.
    """
    from meta_harness.run_service.routing import replace_engine

    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("config:tiers",))
        current = active(conn, "tiers")
        document = json.loads(json.dumps(current["document"]))
        moved = []
        for tier in document.get("tiers", []):
            models = tier.get("models", [])
            tier["models"] = [replace_engine(str(m)) for m in models]
            moved += [f"{old} to {new}" for old, new in zip(models, tier["models"]) if old != new]
        if not moved:
            return []
        check(conn, "tiers", document)
        note = f"moved off an engine a harness cannot use: {'; '.join(moved)}"
        return [_insert(conn, "tiers", document, SHIPPED_BY, note, "shipped", None)]


def _seed(conn: psycopg.Connection, kind: str) -> dict[str, Any]:
    document = KINDS[kind].seed()
    with conn.transaction():
        # Another reader may have seeded it a moment ago.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"config:{kind}",))
        existing = conn.execute(
            f"SELECT {COLUMNS} FROM config.active a JOIN config.config_version v USING (kind, version) "
            "WHERE a.kind = %s",
            (kind,),
        ).fetchone()
        if existing:
            return _row(existing)
        return _insert(conn, kind, document, "seed", "from the deployment's file", "seed", None)


def _insert(
    conn: psycopg.Connection,
    kind: str,
    document: dict[str, Any],
    who: str,
    note: str | None,
    origin: str,
    rolled_back_from: int | None,
) -> dict[str, Any]:
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"config:{kind}",))
    (latest,) = conn.execute(
        "SELECT coalesce(max(version), 0) FROM config.config_version WHERE kind = %s", (kind,)
    ).fetchone()
    version = latest + 1
    conn.execute(
        "INSERT INTO config.config_version (kind, version, document, edited_by, note, origin, rolled_back_from) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (kind, version, Jsonb(document), who, note, origin, rolled_back_from),
    )
    conn.execute(
        "INSERT INTO config.active (kind, version) VALUES (%s, %s) "
        "ON CONFLICT (kind) DO UPDATE SET version = EXCLUDED.version, changed_at = now()",
        (kind, version),
    )
    row = conn.execute(
        f"SELECT {COLUMNS} FROM config.config_version v WHERE v.kind = %s AND v.version = %s", (kind, version)
    ).fetchone()
    return _row(row)


def _known(kind: str) -> None:
    if kind not in KINDS:
        raise KeyError(kind)


# A process that reads configuration keeps the document of the active version, and rereads only
# when the active version moves.
_cache: dict[str, tuple[int, dict[str, Any]]] = {}


def active_document(kind: str) -> tuple[int, dict[str, Any]]:
    """The active version number and document of a kind, from this process's cache when it is current."""
    with db.connect(autocommit=True) as conn:
        version = active_version(conn, kind)
        cached = _cache.get(kind)
        if cached and version is not None and cached[0] == version:
            return cached
        current = active(conn, kind)
    _cache[kind] = (current["version"], current["document"])
    return _cache[kind]
