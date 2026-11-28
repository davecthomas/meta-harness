"""A repo's open security alerts become issues, once each.

A repo's own scans say what its code and its dependencies are open to (docs/specs/work-with-repo-ci.md). An
alert on the code host is read by whoever opens that page, which is nobody, most days. So once a day the planner
reads each registered repo's open code scanning and dependency alerts, and files each new one of high or
critical severity as an issue in the repo's tracker, with no goal's label: an operator takes it into a goal, as
they do a review's follow-ups. Nothing is fixed unasked, since a dependency's fix can break a build.

An alert is filed once (`planner.security_alert`). A repo whose alerts cannot be read, most often because the
GitHub App lacks the permission, is logged with what to grant, and the others are still read.
"""

import asyncio
import logging
from typing import Any

from meta_harness import db, fallback, outbound
from meta_harness.planner import repos
from meta_harness.run_service import code_host, provenance
from meta_harness.trackers.base import TrackerError

log = logging.getLogger("meta_harness.planner")
# How often the alerts are read: once a day.
EVERY_SECONDS = fallback.seconds("MH_SECURITY_ALERTS_SECONDS", 86400.0)
# The severities filed. A lower one stays on the code host.
FILED = ("critical", "high")
# How many are filed for a repo in one pass: a first read of a repo with a long list is not a flood of issues.
MOST = 10
CODE, DEPENDENCY = "code_scanning", "dependency"
KINDS = {CODE: "Code scanning", DEPENDENCY: "Dependency"}


def _known(repo: str) -> set[tuple[str, int]]:
    with db.connect(autocommit=True) as conn:
        rows = conn.execute(
            "SELECT kind, number FROM planner.security_alert WHERE repo = %s", (repo.lower(),)
        ).fetchall()
    return {(row[0], row[1]) for row in rows}


def _remember(repo: str, alert: dict, ref: str) -> None:
    with db.connect(autocommit=True) as conn:
        conn.execute(
            "INSERT INTO planner.security_alert (repo, kind, number, severity, ref) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (repo, kind, number) DO NOTHING",
            (repo.lower(), alert["kind"], alert["number"], alert["severity"], ref),
        )


def title(alert: dict) -> str:
    return f"{KINDS[alert['kind']]} alert, {alert['severity']}: {alert['summary']}"[:200]


def body(repo: str, alert: dict) -> str:
    """The issue's text: what the alert says, where, how severe, and the alert itself on the code host."""
    lines = [
        f"{repo}'s code host has an open {KINDS[alert['kind']].lower()} alert of {alert['severity']} severity.",
        "",
        f"- What: {alert['summary']}",
    ]
    if alert.get("where"):
        lines.append(f"- Where: `{alert['where']}`")
    if alert.get("fix"):
        lines.append(f"- Fixed in: {alert['fix']}")
    lines += [
        f"- The alert: {alert['url']}",
        "",
        "Fix what it reports, or dismiss the alert on the code host with the reason.",
    ]
    return "\n".join(lines)


async def file_for(repo: str, tracker: Any, events: Any) -> list[str]:
    """Files an issue for each of the repo's open alerts of high or critical severity that has none yet. Returns
    the refs of the issues filed.

    Raises:
        code_host.CodeHostError: The alerts could not be read.
    """
    found = await code_host.security_alerts(repo)
    known = await asyncio.to_thread(_known, repo)
    new = [a for a in found if a["severity"] in FILED and (a["kind"], a["number"]) not in known]
    writer = tracker.for_repo(repo) if hasattr(tracker, "for_repo") else tracker
    where = getattr(writer, "scheme", "") or "github"
    filed: list[str] = []
    for alert in new[:MOST]:
        signed = provenance.Provenance(provenance.ISSUE)
        text = await outbound.for_tracker(body(repo, alert), where, outbound.BODY, outbound.FIXED, provenance=signed)
        headed = await outbound.for_tracker(title(alert), where, outbound.TITLE, outbound.FIXED)
        try:
            made = await writer.create_issue(repo, headed, text, [])
        except (TrackerError, ValueError) as err:
            log.error(
                "the security alert %s #%d of %s could not be filed: %s. It is tried again at the next read",
                alert["kind"],
                alert["number"],
                repo,
                err,
            )
            continue
        await asyncio.to_thread(_remember, repo, alert, made.ref)
        events.emit(
            "security_alert_filed",
            {
                "repo": repo,
                "kind": alert["kind"],
                "number": alert["number"],
                "severity": alert["severity"],
                "ref": made.ref,
            },
            actor="planner",
        )
        filed.append(made.ref)
    if len(new) > MOST:
        log.info("%s has %d more new security alerts: they are filed at the next reads", repo, len(new) - MOST)
    return filed


async def file_all(tracker: Any, events: Any) -> list[str]:
    """One pass over every registered repo. A repo that cannot be read is logged, and the rest are read."""
    filed: list[str] = []
    try:
        names = [entry.name for entry in repos.registry().values()]
    except Exception as err:  # noqa: BLE001 - with no registry to read there is no repo to ask about
        log.error("security alerts: the repo registry could not be read: %s", err)
        return filed
    for repo in names:
        if code_host.is_local(repo):
            continue
        try:
            filed += await file_for(repo, tracker, events)
        except code_host.CodeHostError as err:
            log.warning(
                "the security alerts of %s could not be read: %s. Grant the GitHub App read access to code "
                "scanning alerts and Dependabot alerts on it, or turn those alerts on for the repo",
                repo,
                err,
            )
    return filed
