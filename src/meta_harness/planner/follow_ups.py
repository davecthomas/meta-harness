"""A merged pull request's follow-up findings become one issue, so a later pull request can make them.

A review marks each finding blocking or not (docs/technical-design.md, Review loop). A finding that does not
block is a follow-up: it is posted on the pull request and does not hold the merge. A comment on a merged pull
request is where work goes to be forgotten, so once the pull request has merged the sweep files its last
review's follow-ups as one issue in the goal's tracker. The issue has no goal label: the goal that made the
pull request still finishes, and an operator takes the issue into a later goal when they want the work done.

Each closed issue is looked at once. Its link remembers the issue filed (`follow_ups_ref`), or '' when the
pull request left nothing to file.
"""

import asyncio
import logging
from typing import Any

from meta_harness import db, outbound
from meta_harness.planner import splits
from meta_harness.planner.sweep import goal_tracker
from meta_harness.run_service import provenance, writing
from meta_harness.trackers.base import TrackerError

log = logging.getLogger("meta_harness.planner")
EVENT_TYPES = "review_submitted,pull_request_reviewed,pull_request_merged"


def _unfiled(goal_id: str) -> list[dict]:
    with db.connect(autocommit=True) as conn:
        rows = conn.execute(
            "SELECT ref, repo, task_id, title FROM planner.issue_link "
            "WHERE goal_id = %s AND state = 'closed' AND task_id IS NOT NULL AND follow_ups_ref IS NULL",
            (goal_id,),
        ).fetchall()
    return [{"ref": r[0], "repo": r[1], "task_id": r[2], "title": r[3] or r[0]} for r in rows]


def _remember(ref: str, filed: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute("UPDATE planner.issue_link SET follow_ups_ref = %s WHERE ref = %s", (filed, ref))


def left(events: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """The merged pull request's address and the follow-up findings its last review left, from the task's
    events. ("", []) when the pull request did not merge. The last review is the one that let it merge: a
    follow-up an earlier round raised was either fixed since or raised again."""
    ordered = sorted(events, key=lambda e: e["position"])
    merged = next((e for e in ordered if e["event_type"] == "pull_request_merged"), None)
    if merged is None:
        return "", []
    posted = [e for e in ordered if e["event_type"] == "pull_request_reviewed"]
    submitted = [e for e in ordered if e["event_type"] == "review_submitted"]
    if not posted or len(submitted) < len(posted):
        return str((merged["payload"] or {}).get("pr_url") or ""), []
    numbers = (posted[-1]["payload"] or {}).get("follow_ups") or []
    findings = (submitted[len(posted) - 1]["payload"] or {}).get("findings") or []
    kept = [findings[n - 1] for n in numbers if 0 < n <= len(findings)]
    return str((merged["payload"] or {}).get("pr_url") or ""), kept


def body(pr_url: str, findings: list[dict[str, Any]]) -> str:
    """The issue's text: where the findings came from, then each one with its file and line."""
    lines = [
        f"The review of {pr_url} left these findings as follow-ups. They did not block its merge.",
        "",
    ]
    for finding in findings:
        where = f"`{finding['path']}`" + (f" line {finding['line']}" if finding.get("line") else "")
        text = str(finding.get("body") or "").strip()
        lines.append(f"- {where}: {text}" if finding.get("path") else f"- {text}")
    return "\n".join(lines)


async def file(goal: dict, tracker: Any, events: Any, errors: list[str]) -> list[str]:
    """File an issue for each merged pull request of the goal whose last review left follow-ups. Returns the
    refs of the issues filed. A tracker or event service that does not answer is reported, and the next sweep
    looks again."""
    filed: list[str] = []
    if not hasattr(events, "read"):
        return filed
    writer = goal_tracker(tracker, goal)
    for link in await asyncio.to_thread(_unfiled, goal["id"]):
        try:
            found = await asyncio.to_thread(events.read, 0, 2000, task_id=link["task_id"], event_type=EVENT_TYPES)
            pr_url, findings = left(found.get("events") or [])
            if not findings:
                await asyncio.to_thread(_remember, link["ref"], "")
                continue
            where = getattr(writer, "scheme", "") or link["ref"]
            signed = provenance.Provenance(provenance.ISSUE, goal=goal["id"], task=link["task_id"])
            checked = await outbound.for_tracker(body(pr_url, findings), where, outbound.BODY, outbound.GENERATED)
            if payload := writing.record("issue", outbound.writing_outcomes([checked]), goal_id=goal["id"]):
                events.emit("writing_checked", payload, actor="planner", tags={"goal_id": goal["id"]})
            text = await outbound.for_tracker(str(checked), where, outbound.BODY, outbound.FIXED, provenance=signed)
            title = await outbound.for_tracker(
                f"Follow-ups from: {link['title']}"[:200], where, outbound.TITLE, outbound.FIXED
            )
            made = await writer.create_issue(link["repo"], title, text, [])
        except (TrackerError, ValueError) as err:
            errors.append(f"{link['ref']}: its follow-ups could not be filed: {err}")
            continue
        except Exception as err:  # noqa: BLE001 - an event service that did not answer: the next sweep looks again
            log.warning("goal %s: the reviews of %s could not be read: %s", goal["id"], link["ref"], err)
            continue
        await asyncio.to_thread(_remember, link["ref"], made.ref)
        events.emit(
            "follow_ups_filed",
            {
                "ref": made.ref,
                "from_ref": link["ref"],
                "goal_id": goal["id"],
                "pr_url": pr_url,
                "findings": len(findings),
            },
            task_id=link["task_id"],
            actor="planner",
            tags={"goal_id": goal["id"]},
        )
        log.info(
            "goal %s: filed %s for the %d follow-ups of %s", goal["id"], splits.short(made.ref), len(findings), pr_url
        )
        filed.append(made.ref)
    return filed
