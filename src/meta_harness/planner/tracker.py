"""What the planner asks of an issue's text, whichever tracker holds it, and the names the planner has long
imported from here. The trackers themselves are in `meta_harness.trackers`: the interface, an adapter for each,
and the router that picks one for a repo or a ref.
"""

import re

from meta_harness.run_service.code_host import acceptance_checks, issue_paths
from meta_harness.trackers.base import Issue, Tracker, TrackerError
from meta_harness.trackers.github import GitHubTracker, depends_on, parse_ref, ref_for

__all__ = [
    "GitHubTracker",
    "Issue",
    "Tracker",
    "TrackerError",
    "acceptance_checks",
    "depends_on",
    "issue_paths",
    "outside_author",
    "parse_ref",
    "ref_for",
    "task_text",
]

# Those whose issues the planner runs as written: people the repo's owner gave write access.
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
HTML_COMMENT = re.compile(r"<!--.*?(-->|\Z)", re.DOTALL)
MARKER_LINE = re.compile(r"^(\s*)---(?=\s*\S.*---\s*$)", re.MULTILINE)


def outside_author(issue: Issue) -> bool:
    """Whether the issue was opened by someone the repo's owner gave no write access. Its text is then an
    outsider's, and an operator reads it before it becomes an agent's task."""
    return bool(issue.author_association) and issue.author_association not in TRUSTED_ASSOCIATIONS


def task_text(text: str) -> str:
    """An issue's title or body as a task's prompt carries it: without HTML comments, which GitHub does not
    show to the person who labeled the issue, and with no line shaped like the prompt's own `--- task ---`
    markers, which would end the task's block early."""
    return MARKER_LINE.sub(r"\1- - -", HTML_COMMENT.sub("", text or ""))
