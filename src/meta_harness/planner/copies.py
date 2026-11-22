"""Telling two issues of one goal that are the same work.

A goal builds a task for every issue it takes in. On 2026-11-22 a plan's sixteen new tasks were created three
times over, and nothing noticed: the goal built most of its work two or three times, and the last copies
conflicted with their own work on the base. So each issue a goal takes in is given a hash of what its work
is, and one whose hash another issue of the goal already has is held as a copy, naming the first.

Two issues are the same work when their titles match and either they were made from the same plan (their
provenance lines name the same goal and planning task) or their bodies match. The line saying what an issue
waits on and its provenance line are left out of the body: copies made one after another differ in both.
"""

import hashlib
import re

from meta_harness.run_service import provenance

# The least text, under its title, an issue not made from a plan needs before it can be called a copy.
LEAST_TEXT = 40
_LEFT_OUT = re.compile(r"^(?:Provenance:|Depends on:|Split from )", re.IGNORECASE)


def _plain(text: str) -> str:
    return " ".join((text or "").lower().split())


def content_hash(title: str, body: str) -> str:
    """The hash two issues share when they are the same work. Empty for an issue with too little text to say:
    two issues titled alike with nothing under the title are not known to be one piece of work."""
    made_by = next(
        (
            f"plan {said.get('goal', '')} {said.get('task', '')}"
            for said in provenance.parse(body or "")
            if said.get("kind") == provenance.ISSUE and said.get("task")
        ),
        "",
    )
    kept = _plain("\n".join(line for line in (body or "").splitlines() if not _LEFT_OUT.match(line.strip())))
    if not made_by and len(kept) < LEAST_TEXT:
        return ""
    return hashlib.sha256(f"{_plain(title)}\n{made_by or kept}".encode()).hexdigest()


def text_hash(title: str, body: str) -> str:
    """A hash of everything an issue says, its hidden text included: what an operator read when they let an
    outsider's issue run. A change of any character changes it."""
    return hashlib.sha256(f"{title or ''}\n{body or ''}".encode()).hexdigest()
