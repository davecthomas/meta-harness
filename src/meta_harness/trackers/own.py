"""The meta-harness's own tracker: issues kept in the meta-harness and in no outside system.

A goal's plan can hold tasks the planner wrote that no tracker has. An operator may add them to the goal's
tracker when the plan is approved, or keep them here. A goal that uses no tracker keeps all its work here: it
leaves no record anywhere but its pull requests.

An issue here is a row of `planner.own_issue`, and its ref is `mh:<id>`. It is an issue like any other to the
rest of the planner: read, listed by label, held, sized, made a task, and closed when its task's pull request
merges. So nothing outside this adapter knows whether a goal's issue is in Jira, in GitHub or here.

Nothing here calls another service, so no call can fail for a tracker's reasons. An issue that does not exist
is a TrackerError that repeating will not mend.
"""

import asyncio
import json
import re
import secrets
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row

from meta_harness import outbound
from meta_harness import db
from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from meta_harness.trackers.base import (
    DONE,
    STATUSES,
    Issue,
    Source,
    Supported,
    TrackerError,
    refuse_twice,
    with_section,
)

SCHEME = "mh"
REF = re.compile(r"^mh:(?P<id>i-[0-9a-f]{12})$")
# "Depends on: mh:i-0123456789ab, mh:i-..." lines in a body, as the planner writes them.
DEPENDS_LINE = re.compile(r"^\s*depends on:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
DEPENDS_ITEM = re.compile(r"\bmh:i-[0-9a-f]{12}\b")
COLUMNS = "id, number, repo, title, body, labels, state, status, pr_url, updated_at"


def ref_for(issue_id: str) -> str:
    return f"mh:{issue_id}"


def parse_ref(ref: str) -> str:
    """The id a ref names."""
    match = REF.match(ref)
    if not match:
        raise ValueError(f"{ref!r} is not mh:i-<12 hex digits>, a task kept in the meta-harness")
    return match["id"]


def _issue(row: dict[str, Any]) -> Issue:
    body = row["body"] or ""
    depends: list[str] = []
    for line in DEPENDS_LINE.findall(body):
        depends += DEPENDS_ITEM.findall(line)
    ref = ref_for(row["id"])
    return Issue(
        ref=ref,
        repo=row["repo"],
        number=int(row["number"]),
        title=row["title"],
        body=body,
        labels=list(row["labels"] or []),
        state=row["state"],
        depends_on=[dep for dep in dict.fromkeys(depends) if dep != ref],
        checks=acceptance_checks(body),
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
        paths=issue_paths(body),
        # The planner wrote it, or an operator did in the plan: never an outsider's.
        author="meta-harness",
        author_association="OWNER",
    )


class OwnTracker:
    scheme = SCHEME
    # A task's progress is recorded on its issue here, and its merge closes it: nothing else would.
    writes_progress = True

    def __init__(self, connect: Callable[[], psycopg.Connection] | None = None, source: Source | None = None) -> None:
        self.connect = connect or (lambda: db.connect(autocommit=True))
        self.source = source

    def bound(self, source: Source) -> "OwnTracker":
        return OwnTracker(self.connect, source)

    def _all(self, query: str, params: tuple) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall() if cur.description else []

    def _one(self, ref: str) -> dict[str, Any]:
        found = self._all(f"SELECT {COLUMNS} FROM planner.own_issue WHERE id = %s", (parse_ref(ref),))
        if not found:
            raise TrackerError(
                f"{ref} is not a task the meta-harness keeps: it may have been removed. Remove it from the goal.",
                retryable=False,
                status=404,
            )
        return found[0]

    async def list_open(self, repo: str, label: str | None) -> list[Issue]:
        clause, params = ("", (repo,)) if label is None else (" AND %s = ANY(labels)", (repo, label))
        query = f"SELECT {COLUMNS} FROM planner.own_issue WHERE state = 'open' AND lower(repo) = lower(%s){clause} ORDER BY number"
        return [_issue(row) for row in await asyncio.to_thread(self._all, query, params)]

    async def list_assigned(self, repo: str) -> list[Issue]:
        return []

    async def read(self, ref: str) -> Issue:
        try:
            return _issue(await asyncio.to_thread(self._one, ref))
        except ValueError as err:
            raise TrackerError(str(err), retryable=False) from err

    async def ensure_label(self, repo: str, label: str) -> None:
        return None

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> Issue:
        outbound.require(title, "an issue's title", outbound.OWN, title=True)
        outbound.require(body, "an issue's body", outbound.OWN)
        issue_id = f"i-{secrets.token_hex(6)}"
        query = (
            "INSERT INTO planner.own_issue (id, repo, title, body, labels) VALUES (%s, %s, %s, %s, %s) "
            f"RETURNING {COLUMNS}"
        )
        made = await asyncio.to_thread(self._all, query, (issue_id, repo, title, body, list(dict.fromkeys(labels))))
        return _issue(made[0])

    async def comment(self, ref: str, body: str) -> str:
        """Keeps the comment with the issue. There is no page to link to."""
        outbound.require(body, "a comment on an issue", outbound.OWN)
        query = "UPDATE planner.own_issue SET comments = comments || %s::jsonb, updated_at = now() WHERE id = %s RETURNING id"
        note = json.dumps([{"body": body}])
        if not await asyncio.to_thread(self._all, query, (note, parse_ref(ref))):
            await self.read(ref)
        return ""

    async def add_section(self, ref: str, heading: str, text: str) -> None:
        """Adds a `## heading` section to the end of the issue's body, keeping what the body says. The body is
        written only if no one changed it since it was read, so a change made meanwhile is not lost. A body that
        has the heading already is refused and left as it is. Every issue here is the meta-harness's own, so
        there is no label to add first."""
        outbound.require(heading, "a section's heading", outbound.OWN, title=True)
        outbound.require(text, "a section of an issue's body", outbound.OWN)
        row = await asyncio.to_thread(self._one, ref)
        refuse_twice(ref, row["body"] or "", heading)
        body = with_section(row["body"] or "", heading, text)
        query = (
            "UPDATE planner.own_issue SET body = %s, updated_at = now() "
            "WHERE id = %s AND body IS NOT DISTINCT FROM %s RETURNING id"
        )
        if not await asyncio.to_thread(self._all, query, (body, row["id"], row["body"])):
            raise TrackerError(f"{ref}'s body changed while a section was being added to it. Try again.")

    async def mark(self, ref: str) -> None:
        """Every issue here is the meta-harness's own: there is nothing to tell apart."""
        return None

    async def set_status(self, ref: str, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"{status!r} is not one of {', '.join(STATUSES)}")
        row = await asyncio.to_thread(self._one, ref)
        state = "closed" if status == DONE else "open"
        if (row["status"], row["state"]) == (status, state):
            return False
        query = "UPDATE planner.own_issue SET status = %s, state = %s, updated_at = now() WHERE id = %s RETURNING id"
        await asyncio.to_thread(self._all, query, (status, state, row["id"]))
        return True

    async def link_pull_request(self, ref: str, pr_url: str, title: str) -> None:
        query = "UPDATE planner.own_issue SET pr_url = %s, updated_at = now() WHERE id = %s RETURNING id"
        if not await asyncio.to_thread(self._all, query, (pr_url, parse_ref(ref))):
            await self.read(ref)

    def url(self, ref: str) -> str | None:
        """No page outside the console shows the issue."""
        return None

    def repo_of(self, ref: str) -> str:
        """The repo the issue was written for.

        Raises:
            ValueError: The ref is not one of these, or names an issue that is not kept.
        """
        try:
            return str(self._one(ref)["repo"])
        except TrackerError as err:
            raise ValueError(str(err)) from err


SPEC = Supported(
    SCHEME,
    "None: no issue tracking",
    "The goal's tasks are kept in the meta-harness and in no outside tracker. Its pull requests are the only "
    "record outside the meta-harness.",
    build=OwnTracker,
    writes_progress=True,
)
