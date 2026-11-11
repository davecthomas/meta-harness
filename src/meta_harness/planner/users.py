"""The console's users: who may sign in, and as an admin or a regular user.

    GET    /v1/admin/users                 every user, with role, status, who invited them and their last sign-in
    POST   /v1/admin/users                 invite an email, with a role
    PATCH  /v1/admin/users/{email}         set a user's role
    DELETE /v1/admin/users/{email}         deprovision a user: they can no longer sign in
    POST   /v1/admin/users/seed            the edge's first access list, taken only while there are no users
    POST   /v1/admin/users/{email}/signed-in   the edge reports a sign-in

The access list used to be two environment variables on the edge, so adding a person or making one an admin
was a deploy. The users are now rows here, beside each user's settings, and an admin changes them from the
console. The edge asks for the list and decides each request by it (meta_harness.edge.directory); it lets only
an admin reach these paths, and keeps the last two for itself.

There is always an admin: the last one can be neither deprovisioned nor made a regular user.
"""

from typing import Any, Callable, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

ROLES = ("admin", "user")
COLUMNS = "email, role, status, invited_by, invited_at, last_sign_in_at, updated_at"
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserError(ValueError):
    """A change to the users the planner refuses. The message says why."""


class NotFound(LookupError):
    pass


class InviteIn(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=320)
    role: Literal["admin", "user"] = "user"


class RoleIn(BaseModel):
    role: Literal["admin", "user"]


class SeedIn(BaseModel):
    users: list[InviteIn] = Field(max_length=1000)


def _user(row: dict) -> dict:
    for key in ("invited_at", "last_sign_in_at", "updated_at"):
        row[key] = row[key].isoformat() if row[key] else None
    return row


def list_users(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM planner.console_user ORDER BY status = 'deprovisioned', email")
        return [_user(row) for row in cur.fetchall()]


def _other_admins(cur: psycopg.Cursor, email: str) -> int:
    cur.execute(
        "SELECT count(*) AS n FROM planner.console_user WHERE role = 'admin' AND status <> 'deprovisioned' "
        "AND email <> %s",
        (email,),
    )
    return cur.fetchone()["n"]


def invite(conn: psycopg.Connection, email: str, role: str, by: str) -> dict:
    """Let an email sign in, with a role. One who was deprovisioned is invited again; one who may already
    sign in is refused."""
    email = email.strip().lower()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM planner.console_user WHERE email = %s FOR UPDATE", (email,))
        row = cur.fetchone()
        if row and row["status"] != "deprovisioned":
            raise UserError(f"{email} is already a user ({row['status']})")
        cur.execute(
            "INSERT INTO planner.console_user (email, role, status, invited_by) VALUES (%s, %s, 'invited', %s) "
            "ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role, status = 'invited', "
            "invited_by = EXCLUDED.invited_by, invited_at = now(), last_sign_in_at = NULL, updated_at = now() "
            f"RETURNING {COLUMNS}",
            (email, role, by),
        )
        return _user(cur.fetchone())


def set_role(conn: psycopg.Connection, email: str, role: str) -> tuple[dict, str]:
    """Set a user's role. Returns (user, the role before). The last admin stays one."""
    email = email.strip().lower()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT role, status FROM planner.console_user WHERE email = %s FOR UPDATE", (email,))
        row = cur.fetchone()
        if row is None or row["status"] == "deprovisioned":
            raise NotFound(email)
        if row["role"] == "admin" and role != "admin" and _other_admins(cur, email) == 0:
            raise UserError(f"{email} is the only admin: make someone else an admin first")
        cur.execute(
            f"UPDATE planner.console_user SET role = %s, updated_at = now() WHERE email = %s RETURNING {COLUMNS}",
            (role, email),
        )
        return _user(cur.fetchone()), row["role"]


def deprovision(conn: psycopg.Connection, email: str) -> dict:
    """End a user's access. The last admin cannot be deprovisioned."""
    email = email.strip().lower()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT role, status FROM planner.console_user WHERE email = %s FOR UPDATE", (email,))
        row = cur.fetchone()
        if row is None or row["status"] == "deprovisioned":
            raise NotFound(email)
        if row["role"] == "admin" and _other_admins(cur, email) == 0:
            raise UserError(f"{email} is the only admin: make someone else an admin first")
        cur.execute(
            "UPDATE planner.console_user SET status = 'deprovisioned', updated_at = now() WHERE email = %s "
            f"RETURNING {COLUMNS}",
            (email,),
        )
        return _user(cur.fetchone())


def seed(conn: psycopg.Connection, users: list[tuple[str, str]], by: str) -> int:
    """Take the edge's first access list, only while there are no users at all. Returns how many were added."""
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("LOCK TABLE planner.console_user IN EXCLUSIVE MODE")
        cur.execute("SELECT count(*) AS n FROM planner.console_user")
        if cur.fetchone()["n"]:
            return 0
        for email, role in users:
            # They were on the list before: they are users already, not invitations waiting.
            cur.execute(
                "INSERT INTO planner.console_user (email, role, status, invited_by) VALUES (%s, %s, 'active', %s) "
                "ON CONFLICT (email) DO NOTHING",
                (email.strip().lower(), role, by),
            )
        return len(users)


def signed_in(conn: psycopg.Connection, email: str) -> None:
    """A user signed in: an invited one is active from now."""
    with conn.transaction():
        conn.execute(
            "UPDATE planner.console_user SET status = 'active', last_sign_in_at = now(), updated_at = now() "
            "WHERE email = %s AND status <> 'deprovisioned'",
            (email.strip().lower(),),
        )


def register(app: FastAPI, operator: Callable[..., str], events: Any) -> None:
    """Adds the users routes. The edge lets only an admin reach them."""

    def refused(err: Exception) -> HTTPException:
        if isinstance(err, NotFound):
            return HTTPException(status_code=404, detail=f"{err.args[0]} is not a user")
        return HTTPException(status_code=409, detail=str(err))

    @app.get("/v1/admin/users")
    def users(who: str = Depends(operator)) -> dict:
        with app.state.pool.connection() as conn:
            return {"users": list_users(conn)}

    @app.post("/v1/admin/users", status_code=201)
    def invite_user(body: InviteIn, who: str = Depends(operator)) -> dict:
        try:
            with app.state.pool.connection() as conn:
                user = invite(conn, body.email, body.role, who)
        except UserError as err:
            raise refused(err) from err
        events.emit("user_invited", {"email": user["email"], "role": user["role"]}, actor=who)
        return user

    @app.post("/v1/admin/users/seed")
    def seed_users(body: SeedIn, who: str = Depends(operator)) -> dict:
        with app.state.pool.connection() as conn:
            added = seed(conn, [(u.email, u.role) for u in body.users], who)
            return {"added": added, "users": list_users(conn)}

    @app.post("/v1/admin/users/{email}/signed-in")
    def user_signed_in(email: str, who: str = Depends(operator)) -> dict:
        with app.state.pool.connection() as conn:
            signed_in(conn, email)
        return {"email": email.lower()}

    @app.patch("/v1/admin/users/{email}")
    def change_role(email: str, body: RoleIn, who: str = Depends(operator)) -> dict:
        try:
            with app.state.pool.connection() as conn:
                user, before = set_role(conn, email, body.role)
        except (UserError, NotFound) as err:
            raise refused(err) from err
        if before != user["role"]:
            changed = {"email": user["email"], "from": before, "to": user["role"]}
            events.emit("user_role_changed", changed, actor=who)
        return user

    @app.delete("/v1/admin/users/{email}")
    def deprovision_user(email: str, who: str = Depends(operator)) -> dict:
        try:
            with app.state.pool.connection() as conn:
                user = deprovision(conn, email)
        except (UserError, NotFound) as err:
            raise refused(err) from err
        events.emit("user_deprovisioned", {"email": user["email"]}, actor=who)
        return user
