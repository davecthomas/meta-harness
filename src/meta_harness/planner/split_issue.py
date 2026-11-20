"""Splitting a held issue into the parts that run in its place: one path for the operator's Split and the
planner's own (auto_split.py).

Each part must be a complete issue (splits.lacking), gets the goal's label and a `Split from` line, and is
created in the held issue's repo. The split is claimed first, so it happens once, and what was created is
recorded as the split even when the tracker refuses partway, so nothing is created twice. The original gets a
comment naming its parts (splits.announce).
"""

import asyncio
from typing import Any

from meta_harness import outbound
from meta_harness import db
from meta_harness.planner import splits, store
from meta_harness.planner.sweep import goal_tracker
from meta_harness.run_service import provenance, writing
from meta_harness.trackers.base import TrackerError


class SplitRefused(Exception):
    """A split that did not happen, or happened only partly. status is the HTTP status for an API to answer
    with; the message says why and what to do."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


async def split_issue(
    goal: dict, link: dict, parts: list[tuple[str, str]], tracker: Any, events: Any, who: str
) -> list[str]:
    """Create the parts (title, body) the held issue `link` splits into. Returns their refs."""
    ref, goal_id = link["ref"], goal["id"]
    short = [f"{title}: needs {' and '.join(lack)}" for title, body in parts if (lack := splits.lacking(body))]
    if short:
        raise SplitRefused(
            422, f"each part must be a complete issue. Add what each lacks, then split again: {'; '.join(short)}"
        )
    repo, label = link["repo"], goal["label"]

    def claim() -> bool:
        with db.connect(autocommit=True) as conn:
            return store.claim_split(conn, goal_id, ref)

    def record(created: list[str]) -> list[str]:
        with db.connect(autocommit=True) as conn:
            # A goal with no label takes the new issues by ref, as an operator's pick.
            added = [] if label or not created else store.add_refs(conn, goal_id, [(r, repo) for r in created])
            store.record_split(conn, ref, created, splits.reason(created) if created else None)
            return added

    if link.get("split_into") is not None or not await asyncio.to_thread(claim):
        done = ", ".join(link.get("split_into") or [])
        raise SplitRefused(409, f"{ref} was split already" + (f" into {done}" if done else ""))

    def audit(created: list[str], added: list[str]) -> None:
        """The events of a recorded split: each issue a labelless goal took in, then the split itself."""
        for made in added:
            events.emit("issue_added", {"ref": made, "goal_id": goal_id}, actor=who, tags={"goal_id": goal_id})
        if created:
            events.emit(
                "issue_split", {"ref": ref, "goal_id": goal_id, "issues": created}, actor=who, tags={"goal_id": goal_id}
            )

    writer = goal_tracker(tracker, goal)
    created: list[str] = []
    try:
        if label:
            await writer.ensure_label(repo, label)
        # Each part says it came from the goal, and who split it: an operator, or the planner itself.
        signed = provenance.Provenance(provenance.ISSUE, goal=goal_id, by=who)
        where = getattr(writer, "scheme", "") or ref
        for title, body in parts:
            # The part's own words go through the writing pass; its Split from line and provenance are added
            # after, and the whole goes out as composed.
            checked = await outbound.for_tracker(body, where, outbound.BODY, outbound.GENERATED)
            if payload := writing.record("issue", outbound.writing_outcomes([checked]), goal_id=goal_id):
                events.emit("writing_checked", payload, actor="planner", tags={"goal_id": goal_id})
            text = await outbound.for_tracker(
                splits.with_origin(str(checked), ref), where, outbound.BODY, provenance=signed
            )
            name = await outbound.for_tracker(title.strip(), where, outbound.TITLE, outbound.GENERATED)
            made = await writer.create_issue(repo, name, text, [label] if label else [])
            created.append(made.ref)
    except TrackerError as err:
        # What was created is recorded as the split, so a retry never creates it twice; none: it may be split again.
        audit(created, await asyncio.to_thread(record, created))
        done = f"; created before it refused: {', '.join(created)}" if created else ""
        raise SplitRefused(502, f"the tracker refused an issue: {err}{done}") from err
    except Exception:
        audit(created, await asyncio.to_thread(record, created))
        raise
    audit(created, await asyncio.to_thread(record, created))
    await splits.announce(writer, ref, created, goal_id=goal_id, by=who)
    return created
