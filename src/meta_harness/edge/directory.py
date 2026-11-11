"""The console's users as the edge knows them: who may sign in, and who is an admin.

The planner keeps the users (meta_harness.planner.users) and an admin changes them from the console. The edge
reads the list and decides each request by it, rereading every few seconds, so a deprovisioned user's session
ends at their next request and a new role takes effect at once.

Until there are users, the edge's first access list decides, as it always did: MH_CONSOLE_OPERATORS and
MH_CONSOLE_ADMINS. The first time the edge finds the planner with no users, it hands that list over (the seed),
and from then on the planner's list decides and the two variables are read only to seed. A planner that cannot
be reached changes nothing: the edge goes on with the list it last read.
"""

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("meta_harness.edge")
# How long a read of the users serves requests before the edge reads again.
REFRESH_SECONDS = 10.0
TIMEOUT_SECONDS = 3.0


class Directory:
    def __init__(
        self,
        base_url: str | None,
        service_key: str,
        operators: set[str],
        admins: set[str],
        transport: httpx.BaseTransport | None = None,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        """base_url None: there is no planner to ask, and the first access list decides (tests, mostly)."""
        self.operators, self.admins = operators, admins
        self.refresh_seconds = refresh_seconds
        self.http = (
            httpx.Client(
                base_url=base_url,
                timeout=TIMEOUT_SECONDS,
                transport=transport,
                headers={"Authorization": f"Bearer {service_key}", "X-MH-Actor": "edge"},
            )
            if base_url
            else None
        )
        # The users last read, by email; None until a read has found some.
        self.users: dict[str, dict[str, Any]] | None = None
        self.read_at = 0.0
        self.lock = threading.Lock()

    def _read(self) -> None:
        if self.http is None or time.monotonic() - self.read_at < self.refresh_seconds:
            return
        with self.lock:
            if time.monotonic() - self.read_at < self.refresh_seconds:
                return
            # Whatever comes of it, do not ask again for a while: a planner that is down is not asked per request.
            self.read_at = time.monotonic()
            try:
                found = self.http.get("/v1/admin/users").raise_for_status().json()["users"]
                if not found and self.operators:
                    seed = [
                        {"email": email, "role": "admin" if not self.admins or email in self.admins else "user"}
                        for email in sorted(self.operators)
                    ]
                    found = (
                        self.http.post("/v1/admin/users/seed", json={"users": seed}).raise_for_status().json()["users"]
                    )
                    log.warning("handed the planner the console's first %d users", len(found))
                if found:
                    self.users = {user["email"]: user for user in found}
            except Exception as err:  # noqa: BLE001 - the list last read goes on deciding
                log.warning("could not read the console's users: %s", err)

    def allowed(self, who: str) -> bool:
        """Whether this identity may use the console."""
        self._read()
        if self.users is None:
            return who in self.operators
        return who in self.users and self.users[who]["status"] != "deprovisioned"

    def is_admin(self, who: str) -> bool:
        self._read()
        if self.users is None:
            # No admin list: every operator is an admin, so a deploy before the list is set changes nothing.
            return not self.admins or who in self.admins
        return self.allowed(who) and self.users[who]["role"] == "admin"

    def signed_in(self, who: str) -> None:
        """Tells the planner of a sign-in, so an invited user becomes active. A failure only leaves that for later."""
        if self.http is None:
            return
        try:
            self.http.post(f"/v1/admin/users/{who}/signed-in").raise_for_status()
        except Exception as err:  # noqa: BLE001
            log.warning("could not record %s's sign-in: %s", who, err)

    def changed(self) -> None:
        """An admin changed the users through this edge: read them again at the next request."""
        self.read_at = 0.0
