"""Names and payload types shared by the workflow, the activities, the worker and the starter.

Payloads are dataclasses. The SDK's default data converter turns them into JSON
and back, using the type hints on the workflow and activity signatures.
"""

import os
from dataclasses import dataclass, field
from typing import Any

TASK_QUEUE = "run-task"
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
DEFAULT_TEMPLATE = "hello-v1"
# The template name a run reports when the caller gave the task text itself.
INLINE_TEMPLATE = "inline"
# Most a task may spend on model calls across all its runs, in USD, when the task names no max_usd.
# Each run's grant gets what is left. A standard-tier run on these repos costs a few dollars.
DEFAULT_SPEND_LIMIT_USD = "10.00"


WORKFLOW_ID_PREFIX = "run-task-"


def workflow_id_for(ref: str) -> str:
    """The caller's task reference becomes the workflow id, so a REF names one task."""
    return f"{WORKFLOW_ID_PREFIX}{ref}"


def ref_for(workflow_id: str) -> str:
    """The REF back from a workflow id. It names the task's directory under runs/."""
    return workflow_id.removeprefix(WORKFLOW_ID_PREFIX)


def run_id_for(workflow_id: str, attempt: int) -> str:
    """One run per tier candidate tried: the workflow id and the attempt number.

    The workflow passes it to run_harness, so every Temporal retry of one
    attempt uses the same run id and re-attaches to the same run.
    """
    return f"{workflow_id}:run-{attempt}"


@dataclass
class RunTaskInput:
    """What the starter asks for. Secrets stay out: workflow history is visible in the web UI."""

    # The task text. When empty, prepare reads prompts/<template>.txt instead.
    task: str = ""
    template: str = DEFAULT_TEMPLATE
    spend_limit_usd: str = DEFAULT_SPEND_LIMIT_USD
    # A harness-profile-and-model pair, harness:<profile>/<engine>:<model>, run with no routing.
    pin: str = ""
    # Router tiers the task may not route below or above.
    floor: str = ""
    ceiling: str = ""
    # The repo pi works in: owner/name on GitHub, or the path of a local bare git repo. Empty: an empty workspace.
    repo: str = ""
    # A GitHub issue, owner/name#number: the task text and checks come from it, and the pull request closes it.
    ticket: str = ""
    # Acceptance checks, when the ticket names none or there is no ticket.
    checks: list[str] = field(default_factory=list)
    # Set when the task came through the Task API: the workflow loads the rest from the task service,
    # moves the task's status, and records its routing and runs.
    task_id: str = ""
    # A Task API task's own title, so prepare reads no ticket, and the issue its pull request closes.
    title: str = ""
    closes: str = ""
    # Whether an opened pull request goes through the review loop: checks, review, fixes.
    review: bool = True
    # The task's goal, whose auto_merge decides whether a clean pull request merges.
    goal_id: str = ""
    # Attempts made at once on the first tier's candidates, for an operator to pick one. 1: the usual run loop.
    attempts: int = 1


@dataclass
class Candidate:
    """One harness-profile-and-model pair, with its harness profile read from harness_profiles.json."""

    label: str
    profile_name: str
    engine: str
    model: str
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedRun:
    """The prepare activity's output: the prompt pi will run, and the pinned pair if any."""

    template: str
    prompt: str
    pinned: Candidate | None = None
    # Set for a repo task: where pi works, the ticket it closes, and the pull request's title.
    repo: str = ""
    ticket: str = ""
    title: str = ""
    # The task's own text and acceptance checks, for the review loop's prompts.
    description: str = ""
    checks: list[str] = field(default_factory=list)


@dataclass
class RouteRequest:
    prompt: str
    floor: str = ""
    ceiling: str = ""


@dataclass
class RouteResult:
    """The route activity's output, from the ai-api-unified router."""

    tier: str
    method: str
    confidence: float | None
    candidates: list[Candidate]
    # Every tier, lowest first, and what to do when a tier's candidates are used up.
    tiers: list[str]
    on_exhausted: str
    classifier_usd: str
    config_id: str | None
    # The tier's thinking level, else the router's; None when neither sets one.
    thinking: str | None = None


@dataclass
class HarnessRequest:
    run_id: str
    prompt: str
    # The run grant pins the candidate's engine and model. The harness server never sees them.
    candidate: Candidate
    spend_limit_usd: str = DEFAULT_SPEND_LIMIT_USD
    # The repo the Lease step clones into the run's workspace before pi starts. Empty: an empty workspace.
    repo: str = ""
    # Set for a Task API task: the Wait step forwards the run's events to the event service.
    task_id: str = ""
    # The routing tier's thinking level, sent on every turn. None: the HTTP Gateway's default.
    thinking: str | None = None
    # The branch to clone, for a review or fix run on a pull request. Empty: the repo's default branch.
    branch: str = ""
    # For a fix run on a merge conflict: the base branch the host merges into the clone first, leaving
    # the conflict markers for the run to resolve.
    merge_base: str = ""


@dataclass
class PublishRequest:
    run_id: str
    repo: str
    ticket: str
    title: str
    candidate: str
    summary: str | None
    spent_usd: str
    # The branch to push, for a fix after a merge. Empty: mh/<REF>.
    branch: str = ""
    # A paragraph that opens the pull request's description, such as which merge it fixes.
    note: str = ""


@dataclass
class PublishResult:
    """The branch and pull request the publish activity made. pr_url is empty when pi changed nothing."""

    branch: str
    base: str
    commit: str
    files: list[str]
    pr_url: str
    created: bool


@dataclass
class HarnessResult:
    """What the harness server reported for the run: its run_finished event and GET /usage."""

    attempt: int
    run_id: str
    status: str
    end_reason: str
    reattached: bool
    turns: int
    failed_calls: int
    tokens_in: int
    tokens_out: int
    usd: float
    files_changed: list[str]
    answer: str | None
    # The run grant's limit, and what the HTTP Gateway counted against it when the grant was revoked.
    spend_limit_usd: str = DEFAULT_SPEND_LIMIT_USD
    grant_spent_usd: str | None = None
    error: str | None = None
    # A review run's verdict, from submit_review: {"verdict", "summary", "findings"}. None for any other run.
    review: dict[str, Any] | None = None


@dataclass
class CleanupResult:
    """What the cleanup activity found when it closed a run that run_harness could not finish."""

    grant_spent_usd: str | None


@dataclass
class Attempt:
    """One tier candidate the workflow tried, and how the Decide step read the outcome."""

    attempt: int
    tier: str | None
    candidate: str
    run_id: str
    # finished, next_candidate (an infrastructure failure), or final (a limit, or a refused config)
    decision: str
    detail: str
    spent_usd: str


@dataclass
class RunTaskResult:
    template: str
    prompt: str
    route: RouteResult | None
    attempts: list[Attempt]
    harness: HarnessResult
    spent_usd: str
    published: PublishResult | None = None
    # How the review loop ended: clean, unreviewed or review_unresolved, and why. Empty when it did not run.
    outcome: str = ""
    outcome_reason: str = ""


@dataclass
class TaskSpec:
    """A task from the task service, as the RunTask workflow needs it."""

    task_id: str
    status: str
    repo: str
    title: str
    description: str
    checks: list[str]
    pin: str
    floor: str
    ceiling: str
    spend_limit_usd: str
    # The issue the pull request closes, owner/name#number, from a github: external_ref.
    closes: str
    goal_id: str = ""
    attempts: int = 1


@dataclass
class TaskStatusRequest:
    task_id: str
    status: str
    reason: str = ""
    pr_url: str = ""


@dataclass
class RouteRecord:
    """One routing decision, for the run service's routing_decision table and a task_routed event."""

    task_id: str
    route_no: int
    floor: str
    ceiling: str
    route: RouteResult


@dataclass
class AttemptRecord:
    """One run, for the run service's run table and an attempt_finished event."""

    task_id: str
    attempt: Attempt
    candidate: Candidate
    harness: HarnessResult | None = None


@dataclass
class PublishedRecord:
    task_id: str
    published: PublishResult


# --- The review loop --------------------------------------------------------------------------------


@dataclass
class ChecksRequest:
    repo: str
    head_sha: str
    task_id: str = ""


@dataclass
class FailedCheck:
    name: str
    summary: str


@dataclass
class MergeChecksRequest:
    """The checks on a merge commit to wait for. base: the branch it merged into, whose newer commits stand in for
    a check a later merge canceled."""

    repo: str
    merge_sha: str
    base: str
    task_id: str = ""
    round_no: int = 0


@dataclass
class ChecksResult:
    """How the code host's checks on a commit ended: passed, failed, none (the repo runs no checks), or pending
    (a merge commit's checks still running when the wait gave up)."""

    state: str
    failed: list[FailedCheck] = field(default_factory=list)


@dataclass
class LoopPromptRequest:
    """What a review or fix run's prompt is built from. kind: review, or fix."""

    kind: str
    repo: str
    title: str
    description: str
    checks: list[str]
    base: str
    branch: str
    round_no: int = 0
    # What the fix run answers: the review's findings and summary, or the checks that failed.
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    failed_checks: list[FailedCheck] = field(default_factory=list)
    # The branch conflicts with its base: the fix run resolves the merge the host started.
    conflict: bool = False
    # A fix after a merge: the merged pull request whose merge commit failed failed_checks.
    merged_pr: str = ""
    # A review after the first: the round before's findings and summary, which this round judges first.
    previous_findings: list[dict[str, Any]] = field(default_factory=list)
    previous_summary: str = ""


@dataclass
class ReviewPolicy:
    """The task policy's review rules, with each reviewer pair resolved against the active harness profiles."""

    reviewers: list[Candidate]
    max_rounds: int
    # Reviewer pairs that could not be resolved, and why: shown when no reviewer is left.
    unresolved: list[str] = field(default_factory=list)


@dataclass
class PostReviewRequest:
    task_id: str
    repo: str
    pr_url: str
    head_sha: str
    reviewer: str
    review: dict[str, Any]
    round_no: int


@dataclass
class MergeRequest:
    task_id: str
    repo: str
    pr_url: str
    branch: str
    base: str
    # The reviewed head commit: GitHub merges only if the pull request's head is still this commit.
    head_sha: str


@dataclass
class MergeResult:
    """merged, head_changed (a commit landed after the review), conflict, or blocked (GitHub refused)."""

    state: str
    merge_sha: str = ""
    # The pull request's head now: a new commit when the state is head_changed.
    head_sha: str = ""
    detail: str = ""
    # With head_changed: the pull request's own diff against its base is what it was at the reviewed head,
    # as when the base was brought in and touched none of its files. The clean review still stands.
    same_diff: bool = False


@dataclass
class OperatorMessage:
    """An operator's word to a running task: a message for its next turn, one for after it stops, or stop."""

    message_id: int
    text: str
    # next_turn, after_stop, or stop (end the run after the turn in progress, keeping its work).
    when: str


@dataclass
class DeliverRequest:
    run_id: str
    message: OperatorMessage
