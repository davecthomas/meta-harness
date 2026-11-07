# Meta-Harness Requirements

Derived from [technical-design.md](technical-design.md), which wins where the
two disagree. Every requirement here is one the system must meet. Whether
each is built yet is in the design's component table and build plan.

## Purpose

A control plane that turns goals into merged pull requests. An operator sets
a goal in the cloud console; the meta-harness takes the goal's labeled issues
across one or more repos as tasks, routes each task to a harness profile, a
model and a thinking level, runs the harness in an isolated per-run sandbox,
meters every model call, and records the outcome.

## Functional requirements

**Goals and planning**

- FR-1. Create, change, pause, resume and cancel goals through the REST API.
  A goal names its repos, its label, its modality (`fix`, `feature` or
  `new-system`), its latitude, `auto_merge`, its budget and its priority.
- FR-2. Turn each open issue with a goal's label in one of its repos into a
  task, with its acceptance checks and its `Depends on:` issues, and refuse
  an issue with no acceptance checks, saying why.
- FR-3. For `feature` and `new-system` goals, run a planning task that drafts
  issues, hold the plan for an operator's approval, and create the approved
  issues on the tracker.

**Task intake**

- FR-4. Accept tasks via `POST /v1/tasks`, idempotent on `external_ref`, and
  let a caller find its task by `external_ref`.
- FR-5. Validate the task schema: repo, task type, priority, title,
  description, acceptance checks, and optional paths, complexity, max tier,
  max USD and pin.
- FR-6. Report task state, routing decision, pull request URL, outcome and
  cost; stream run events; support cancel.

**Scheduling**

- FR-7. Admit queued tasks by priority, then age, while global run
  concurrency, the goal's budget and the task's dependencies allow it.
- FR-8. Lock the repo area (repo plus named top-level directories, or the
  whole repo when unscoped) so two runs never edit the same area.
- FR-9. Stop every running task, and admit none, while the kill switch is on.

**Routing**

- FR-10. Classify each task's tier with the ai-api-unified router in
  classify-only mode (`AiRouteClassifier`), which returns the tier's ordered
  harness-profile-and-model candidates and its thinking level, and calls no
  answering model.
- FR-11. Try a tier's candidates in order. A run that failed on
  infrastructure moves to the next candidate; a run that ended on a limit
  ends the task. When a tier is used up, escalate one tier up, never down.
- FR-12. Honor caller hints: `complexity: high` sets the floor, `max_tier`
  sets the ceiling, and `pin` runs one pair with no routing.
- FR-13. Cap a task's model spend across all its runs at its `max_usd`: each
  run's grant gets what the task has left.

**Execution**

- FR-14. Run one harness per run in its own per-run sandbox, through the
  harness server Run API: health, run, followup, events, status, cancel,
  usage and bundle.
- FR-15. Emit typed events from the harness server, each with run id,
  sequence number and timestamp.
- FR-16. Bound each run by its harness profile's turn and minute limits, and
  its bash tool by the profile's allowlist.
- FR-17. Publish a finished run's changes as a pull request that closes its
  issue, from the control plane, never from the sandbox.
- FR-18. Review every pull request with a meta-harness review task on a
  different model than the one that wrote it. Ticket failed required checks
  and review findings as fix tasks on the same branch, continuing the pull
  request's session, until a review comes back clean, then merge at the
  reviewed commit when the goal's `auto_merge` is on. Stop at the task
  policy's fix-task limit or the task's budget and leave the pull request
  open.
- FR-19. Let an operator take over any running task, and hand it back or
  close it.

**HTTP Gateway (ai-api-unified-http)**

- FR-20. Issue run grants that pin a run's model, cap its spend and expire,
  and answer every model turn a run makes on its grant key alone.
- FR-21. Refuse a turn once the run has spent its grant's limit, with a code
  the harness reads as a spend-limit end.

**Metering and record**

- FR-22. Record every task, routing decision, run, run event and outcome in
  the event service, with the actor of every operator action.
- FR-23. Compute dollar-hours per successfully completed task,
  `Σ(Di × Hi) / ΣCi`, counting failed attempts.
- FR-24. Reconcile harness-reported usage against the HTTP Gateway's counted
  spend and flag gaps over 5%.
- FR-25. An analyzer reports cost and success by harness profile, model,
  thinking level and task type, and proposes configuration changes an
  operator approves.

## Non-functional requirements

- NFR-1. **No hard-coded models or harnesses.** Router tiers, harness
  profiles and the repo registry live in configuration.
- NFR-2. **Single model path.** Every model call goes through ai-api-unified.
  No provider key exists in a per-run sandbox.
- NFR-3. **API-only harness control.** No CLI driving or stdout parsing.
- NFR-4. **Fail at startup.** Each service checks its configuration at
  startup and names the bad setting.
- NFR-5. **Durability.** Runs last minutes to hours. A worker may die and the
  run re-attaches by run id on retry. Starting a task is idempotent.
- NFR-6. **Horizontal scale.** Throughput grows by adding sandbox capacity and
  HTTP Gateway replicas, with no design change.
- NFR-7. **Cloud first.** The system runs in Google Cloud with nothing on an
  operator's machine. Local runs exist for development only.
- NFR-8. **Pluggable harnesses.** Adding one means an image that serves the
  harness server Run API.

## Harness admission criteria

A harness must have a programmatic API (start, follow up, stream, cancel),
structured events, resumable sessions, a model layer whose calls can all be
sent to the HTTP Gateway, and headless operation. The default harness is pi.

## Security requirements

- SEC-1. Repo-scoped code-host tokens per run: read-only for the clone,
  write only for the control plane's Publish step, each expiring within an
  hour.
- SEC-2. A per-run sandbox holds only its run grant key and no other secret.
  Nothing it writes runs on the control plane's host.
- SEC-3. Treat task text, review comments and CI logs as untrusted: a
  delimited data section in the prompt, and the HTTP Gateway's injection
  checks once they are on.
- SEC-4. Sandbox egress reaches only the code host, package registries and
  the HTTP Gateway.
- SEC-5. Only operators on the access list reach the console and the REST
  API, through Identity-Aware Proxy.
- SEC-6. Audit every task launch, cancel, kill switch, takeover and
  configuration change with its actor.

## Constraints

Python 3.12, FastAPI, Temporal, Postgres, Redis, and a React and TypeScript
console. Per-run sandboxes run on Docker on one Compute Engine VM today and
on GKE pods with gVisor later; a fake executor serves tests.

## Out of scope

Difficulty classification, model selection, fallback and pricing, which
ai-api-unified owns; writing a coding agent; deploying merged code.

## Open questions

1. Managed sandbox service or GKE, on cost and start latency.
2. Does per-call routing within a tier range change harness results? Needs
   an A/B.
