"""A task's review rounds on its pull request, from the events its RunTask workflow recorded.

Each round posts its review (`pull_request_reviewed`: round, verdict,
reviewer, reviewed commit, review URL) after the review run submitted it
(the harness's `review_submitted`: verdict, summary, findings). The two
pair up in order. What came next is read from what the workflow recorded
after the round: a fix run, the merge, or the task's end.
"""

from typing import Any

REVIEW_EVENT_TYPES = (
    "review_submitted",
    "pull_request_reviewed",
    "attempt_finished",
    "pull_request_merged",
    "finding_dismissed",
)


def rounds(task: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The task's review rounds, oldest first, each with what came next."""
    ordered = sorted(events, key=lambda e: e["position"])
    submitted = [e for e in ordered if e["event_type"] == "review_submitted"]
    posted = sorted(
        (e for e in ordered if e["event_type"] == "pull_request_reviewed"), key=lambda e: e["payload"]["round"]
    )
    # Each dismissal by an operator, by round and finding number (from 1): the newest for a finding stands.
    dismissed: dict[tuple[int, int], dict[str, Any]] = {}
    for event in ordered:
        if event["event_type"] == "finding_dismissed":
            payload = event["payload"] or {}
            dismissed[(payload.get("round"), payload.get("finding"))] = {
                "by": event.get("actor"),
                "reason": payload.get("reason") or "",
            }
    shown = []
    for index, review in enumerate(posted):
        body = (submitted[index]["payload"] if index < len(submitted) else {}) or {}
        run_id = submitted[index].get("run_id") if index < len(submitted) else None
        # What a round led to is what happened before the next round, not anything later in the task.
        until = posted[index + 1]["position"] if index + 1 < len(posted) else None
        after = [e for e in ordered if e["position"] > review["position"] and (until is None or e["position"] < until)]
        shown.append(
            {
                "round": review["payload"]["round"],
                "verdict": review["payload"]["verdict"],
                "reviewer": review["payload"].get("reviewer"),
                "head_sha": review["payload"].get("head_sha"),
                "summary": body.get("summary") or "",
                "findings": [
                    {
                        "n": n,
                        "path": f.get("path"),
                        "line": f.get("line"),
                        "body": f.get("body", ""),
                        "dismissed": dismissed.get((review["payload"]["round"], n)),
                    }
                    for n, f in enumerate(body.get("findings") or [], 1)
                ],
                "review_url": review["payload"].get("review_url"),
                "reviewed_at": review.get("time_utc"),
                "run_id": run_id,
                "next": _next(task, review, after, is_last=index == len(posted) - 1),
            }
        )
    return shown


def _next(task: dict, review: dict, after: list[dict], is_last: bool) -> dict[str, Any]:
    """What the round led to: a fix run, the merge, waiting on an operator, or the loop's end."""
    fix = next(
        (e for e in after if e["event_type"] == "attempt_finished" and (e["payload"] or {}).get("tier") == "fix"), None
    )
    if review["payload"]["verdict"] == "changes" and fix:
        return {"kind": "fix", "run_id": fix.get("run_id"), "attempt": fix["payload"].get("attempt")}
    if any(e["event_type"] == "pull_request_merged" for e in after):
        return {"kind": "merged"}
    if not is_last:
        # A clean round is reviewed again when the pull request's head changed before it could merge.
        return {"kind": "reviewed_again"}
    if task["status"] == "ready":
        return {"kind": "waiting", "reason": task.get("status_reason")}
    if task["status"] in ("review_unresolved", "failed", "canceled", "closed"):
        return {"kind": "ended", "reason": task.get("status_reason")}
    return {"kind": "under_way"}
