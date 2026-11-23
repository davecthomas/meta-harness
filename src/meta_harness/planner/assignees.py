# Created by the meta-harness in run t-ad0990f39f2b:run-1 on harness:pi-careful/claude:claude-opus-5-5.

"""Who the meta-harness assigns its issues to, and when (docs/technical-design.md, "Who an issue is assigned to").

Who: the person the goal's tracker settings name (`assignee`), for an issue in the goal's own tracker; failing
that, the goal's owner (`created_by`), when the tracker knows a person with that email; failing that, nobody.
`person_for` is the one place that works this out.

When: an issue created from a plan or a split is assigned right after it is created (`assign_new`). An issue a
goal takes in with no assignee is assigned when its task starts (`assign_started`); one that has an assignee
keeps it.

A person the tracker refuses never stops the work: the issue is still created, its task still runs, and an
`issue_not_assigned` event names the issue, the person and what to do. The goal's page shows those among what
needs the operator (`not_assigned`, `needs`).
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

from meta_harness.events.client import stable_event_id
from meta_harness.trackers.base import ASSIGNEE, TrackerError, scheme_of

log = logging.getLogger("meta_harness.planner.assignees")

EVENT = "issue_not_assigned"
# The trackers a goal names by default, and the meta-harness's own, whose people are console users by email.
DEFAULT_TRACKER, OWN = "github", "mh"
# How long a worked-out person is kept before the tracker's people are listed again.
KEEP_SECONDS = 600.0
# A task in one of these has started and has not ended: its issue is assigned now, if it has no one.
STARTED = ("running", "taken_over", "ready", "review_unresolved")
# The most issue_not_assigned events the goal's page reads, newest first.
EVENTS_READ = 500
LAST = 2**62


@dataclass(frozen=True)
class Who:
    """The person an issue is assigned to, as the tracker's `assign` takes them, and where the name came from
    (setting or owner). person is "" for nobody, and why says why."""

    person: str
    source: str = ""
    why: str = ""


# Each goal's person by (goal id, tracker scheme, repo), with when it was worked out.
_known: dict[tuple[str, str, str], tuple[float, Who]] = {}
# The refs whose started task was looked at already: each is assigned, or reported, once per process.
_started: set[str] = set()


def _goal_tracker(goal: dict) -> str:
    return str(goal.get("tracker") or DEFAULT_TRACKER)


def _match(choices: list[dict[str, str]], email: str) -> str:
    """The id of the one listed person with that email: the id itself (the own tracker), or a label that ends
    with it in parentheses (Jira). "" when none or several are."""
    wanted = email.strip().lower()
    found = {
        str(c.get("value") or "")
        for c in choices
        if str(c.get("value") or "").strip().lower() == wanted
        or str(c.get("label") or "").strip().lower().endswith(f"({wanted})")
    }
    found.discard("")
    return found.pop() if len(found) == 1 else ""


async def _people(goal: dict, tracker: Any, repo: str) -> list[dict[str, str]]:
    """The people the issue's own repo can assign (`people(repo)`), so a goal with several repos assigns each
    issue from its repo's list. A tracker without that call is asked for the goal setting's `choices`.

    Raises:
        AttributeError: The tracker lists no people.
    """
    if hasattr(tracker, "people"):
        return await tracker.people(repo) or []
    settings = goal.get("tracker_settings") or {}
    scope = {"repos": ",".join(goal.get("repos") or []), "repo": repo, "project": str(settings.get("project") or "")}
    return await tracker.choices(ASSIGNEE, scope) or []


async def _owner(goal: dict, tracker: Any, scheme: str, repo: str) -> Who:
    owner = str(goal.get("created_by") or "").strip()
    if "@" not in owner:
        return Who("", why=f"the goal's owner {owner!r} is no email the tracker could match, and no assignee is named")
    if scheme == OWN:
        # The own tracker's people are console users by email: the owner is one, or assign says not.
        return Who(owner, "owner")
    try:
        people = await _people(goal, tracker, repo)
    except AttributeError:
        return Who("", why="the tracker lists no people to match the goal's owner against")
    person = _match(people, owner)
    if not person:
        return Who("", why=f"no one the tracker can assign has the goal's owner's email, {owner}")
    return Who(person, "owner")


async def person_for(goal: dict, tracker: Any, ref: str, repo: str) -> Who:
    """Who the issue `ref` (in repo) is assigned to: the goal's `assignee` setting, for an issue in the goal's
    own tracker; else the goal's owner, matched by email among the tracker's people; else nobody.

    Raises:
        TrackerError: The tracker's people could not be listed.
    """
    scheme = scheme_of(ref) or _goal_tracker(goal)
    key = (str(goal["id"]), scheme, repo)
    kept = _known.get(key)
    if kept and time.monotonic() - kept[0] < KEEP_SECONDS:
        return kept[1]
    named = str((goal.get("tracker_settings") or {}).get(ASSIGNEE) or "").strip()
    if named and scheme == _goal_tracker(goal):
        who = Who(named, "setting")
    else:
        who = await _owner(goal, tracker, scheme, repo)
    _known[key] = (time.monotonic(), who)
    return who


def _todo(who: Who, refused: bool) -> str:
    if not refused:
        return (
            "The tracker could not be reached after its retries. Octofusion tries again when the issue's "
            f"task starts, or assign {who.person or 'someone'} in the tracker."
        )
    if who.source == "setting":
        return (
            f"Add {who.person} to the tracker so they can be assigned, or start a new goal that names another "
            "assignee. Until then the goal's issues have no assignee."
        )
    return (
        f"Add {who.person}, the goal's owner, to the tracker so they can be assigned, or start a new goal that "
        "names an assignee. Until then the goal's issues have no assignee."
    )


def report(
    events: Any, goal: dict, ref: str, who: Who, reason: str, refused: bool = True, actor: str = "planner"
) -> None:
    """Records that the issue was not assigned to who, why, and what to do: once for an issue and person.
    refused: the tracker refused the person, so asking again changes nothing."""
    todo = _todo(who, refused)
    payload = {"goal_id": goal["id"], "ref": ref, "person": who.person, "reason": reason, "todo": todo}
    log.warning("goal %s: %s was not assigned to %s: %s", goal["id"], ref, who.person, reason)
    events.emit(
        EVENT,
        payload,
        actor=actor,
        tags={"goal_id": goal["id"]},
        event_id=stable_event_id(EVENT, goal["id"], ref, who.person),
    )


async def _assign(goal: dict, tracker: Any, ref: str, repo: str, events: Any, actor: str) -> bool | None:
    """Assigns the issue to the goal's person. Returns whether it changed; None when the attempt failed in a
    way the next one may not (a transient error that outlasted its retries)."""
    try:
        who = await person_for(goal, tracker, ref, repo)
    except AttributeError:
        return False
    except Exception as err:  # noqa: BLE001 - who is assigned never stops the work; the next start looks again
        owner = Who(str(goal.get("created_by") or ""), "owner")
        report(events, goal, ref, owner, f"the tracker's people could not be listed: {err}", False, actor)
        return None
    if not who.person:
        log.info("goal %s: %s is assigned to no one: %s", goal["id"], ref, who.why)
        return False
    try:
        return bool(await tracker.assign(ref, who.person))
    except AttributeError:
        # A tracker with no assign call assigns no one.
        return False
    except TrackerError as err:
        report(events, goal, ref, who, str(err), not err.retryable, actor)
        return None if err.retryable else False
    except Exception as err:  # noqa: BLE001 - who is assigned never stops the work
        report(events, goal, ref, who, f"{type(err).__name__}: {err}", False, actor)
        return None


async def assign_new(goal: dict, tracker: Any, ref: str, repo: str, events: Any, actor: str = "planner") -> bool:
    """Assigns an issue the meta-harness just created. Returns whether it was assigned. Never raises: a person
    the tracker refuses is reported (`report`), and the issue stays as created."""
    return bool(await _assign(goal, tracker, ref, repo, events, actor))


def pending(links: list[dict]) -> list[dict]:
    """The goal's issues with a task that were not yet looked at for their assignee."""
    return [link for link in links if link.get("task_id") and link["ref"] not in _started]


async def assign_started(
    goal: dict, tracker: Any, links: list[dict], tasks_by_id: dict[str, dict], events: Any
) -> list[str]:
    """Assigns each of the goal's issues whose task has started and that has no assignee. One with an assignee
    keeps it. Returns the refs assigned. Each issue is looked at once; one whose attempt failed for a reason
    that may pass is looked at again by the next sweep."""
    assigned = []
    for link in pending(links):
        task = tasks_by_id.get(link["task_id"])
        if not task or task.get("status") not in STARTED or link.get("state") == "closed":
            continue
        ref = link["ref"]
        try:
            if await tracker.assignee(ref):
                _started.add(ref)
                continue
        except AttributeError:
            _started.add(ref)
            continue
        except Exception as err:  # noqa: BLE001 - read again next sweep
            log.warning("goal %s: could not read who %s is assigned to: %s", goal["id"], ref, err)
            continue
        done = await _assign(goal, tracker, ref, link.get("repo") or "", events, "planner")
        if done is None:
            continue
        _started.add(ref)
        if done:
            assigned.append(ref)
    return assigned


def not_assigned(events: Any, goal_id: str, links: list[dict]) -> list[dict[str, str]]:
    """The goal's open issues an issue_not_assigned event names, newest event first, each with the person and
    what to do. Empty when the events cannot be read: the page shows the rest."""
    open_refs = {link["ref"] for link in links if link.get("state") != "closed"}
    try:
        found = events.read(0, EVENTS_READ, before_position=LAST, event_type=EVENT)["events"]
    except Exception as err:  # noqa: BLE001 - the page goes on without them
        log.warning("goal %s: could not read its %s events: %s", goal_id, EVENT, err)
        return []
    out: dict[tuple[str, str], dict[str, str]] = {}
    for event in sorted(found, key=lambda e: int(e.get("position") or 0), reverse=True):
        payload = event.get("payload") or {}
        if payload.get("goal_id") != goal_id or payload.get("ref") not in open_refs:
            continue
        key = (str(payload["ref"]), str(payload.get("person") or ""))
        out.setdefault(key, {"ref": key[0], "person": key[1], "todo": str(payload.get("todo") or "")})
    return list(out.values())


def needs(found: list[dict[str, str]] | None, already: list[dict]) -> list[dict]:
    """What the goal's page asks of the operator for its unassigned issues: one need for each person, on the
    first issue not assigned to them, unless a need is about that issue already."""
    by_person: dict[str, list[dict[str, str]]] = {}
    for item in found or []:
        by_person.setdefault(item["person"], []).append(item)
    asked = {need.get("ref") for need in already}
    out = []
    for person, items in by_person.items():
        first = items[0]
        if first["ref"] in asked:
            continue
        count = f"{len(items)} issues were" if len(items) > 1 else f"{first['ref']} was"
        out.append(
            {
                "what": f"{count} not assigned to {person}. {first['todo']}",
                "ref": first["ref"],
                "action": "open_issue",
                "task_id": "",
                "pr_url": "",
            }
        )
    return out
