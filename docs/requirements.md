# Meta-Harness Requirements

Derived from [technical-design.md](technical-design.md). "Must" marks a hard
requirement.

## Purpose

A control plane that takes a coding task through an API, routes it to the
right harness, model tier and thinking level, runs the harness in an isolated
sandbox, meters every model call, and records the outcome. It starts at a task
and ends at a pull request plus a recorded outcome.

## Functional requirements

**Task intake**

- FR-1. Accept tasks via `POST /v1/tasks`, idempotent on `external_ref`, and
  let a caller find its task by `external_ref`.
- FR-2. Validate the task schema: repo, task type, priority, title,
  description, acceptance checks, and optional paths, complexity, max tier
  and max USD.
- FR-3. Report task state, routing decision, PR URL, outcome and cost; stream
  run events; support cancel.
- FR-4. Authenticate callers by per-caller key, which sets their concurrency
  quota, daily cap and repo allowlist.

**Scheduling**

- FR-5. Admit tasks by priority, then age, only when global concurrency,
  caller quota, caller daily cap and sandbox capacity allow it.
- FR-6. Lock the repo area (repo plus named top-level directories; whole repo
  if unscoped) so two runs never edit the same area.

**Routing**

- FR-7. Classify each task's tier with ai-api-unified `preview_route`, without
  calling an answering model.
- FR-8. Map `(tier, task_type)` to an ordered list of execution profiles from
  config; fall back down the list when an image or pool is unavailable.
- FR-9. Honor caller hints: `complexity: high` sets the floor, `max_tier` sets
  the ceiling. Retries must route one tier up, never down.
- FR-10. Run a configured share of trivial and standard tasks on challenger
  profiles. Complex and security tasks never explore.
- FR-11. Set each run's spend cap to the tier cost estimate times the
  profile's budget multiplier, or the caller's `max_usd` if lower.

**Execution**

- FR-12. Run one harness per run in its own sandbox, through a uniform Run API
  (health, run, followup, events, status, cancel, usage, metrics).
- FR-13. Emit a common event schema from every adapter, with run id, sequence
  number and timestamp.
- FR-14. Review every PR with a meta-harness review task on a different model
  than the one that wrote it. Ticket failed required checks and review
  findings as meta-harness fix tasks on the same branch, continuing the PR's
  session, until a review comes back clean, then merge the PR at the reviewed
  commit. Stop at the task policy's fix-task limit or the task's budget and
  leave the PR open. No person has to review a PR. When a review starts, the
  operator console offers watching operators a review. An operator who claims
  it blocks that review round until the operator submits a verdict or releases
  the claim; with no claim, the agent's verdict decides.
- FR-15. Enforce per-profile timeouts, with a 4-hour hard ceiling.
- FR-16. Provide a kill switch per run, per caller and globally.

**Gateway (ai-api-unified-http additions)**

- FR-17. OpenAI-compatible facade with tool calls and streaming.
- FR-18. Anthropic Messages facade for the Claude Agent SDK.
- FR-19. Run-scoped tokens carrying run identity, route band and spend cap.
  The gateway must route each call within the band and reject calls once the
  cap is spent.

**Metering and record**

- FR-20. Compute dollar-hours per successfully completed task:
  `Σ(Di × Hi) / ΣCi`, counting failed attempts.
- FR-21. Persist every task, routing decision, run, run event, model call,
  check result and outcome.
- FR-22. Reconcile harness-reported usage against gateway cost events and flag
  gaps over 5%.
- FR-23. Enforce spend caps per call, per run, per caller (warn at 80%) and
  globally.
- FR-24. An offline analyzer reports cost and success by harness, model,
  thinking level and task type, and proposes profile changes.

## Non-functional requirements

- NFR-1. **No hard-coded models or harnesses.** All mappings live in config.
- NFR-2. **Single model path.** Every model call goes through ai-api-unified.
  No provider key exists outside the gateway.
- NFR-3. **API-only harness control.** No CLI driving or stdout parsing.
- NFR-4. **Fail at startup.** Validate every profile's image and tier band at
  startup; stop and name the missing setting.
- NFR-5. **Durability.** Runs last minutes to hours. Any process may die and
  the run resumes from its last recorded step. Launch must be idempotent.
- NFR-6. **Horizontal scale.** Throughput grows by adding sandboxes and
  gateway replicas, on cloud or local models, with no design change.
- NFR-7. **Sandbox start** under 30 seconds with a warm pool.
- NFR-8. **Observability.** One OpenTelemetry trace per run, from task to PR.
- NFR-9. **Pluggable harnesses.** Adding one means an image and an adapter.

## Harness admission criteria

A harness must have: a programmatic API (start, follow up, stream, status,
cancel), structured events, resumable sessions, a configurable OpenAI- or
Anthropic-compatible base URL, and headless operation. Initial candidates:
OpenHands, Pi, OpenCode, Codex, Claude Agent SDK.

## Security requirements

- SEC-1. Repo-scoped code-host tokens minted per run, expiring within an hour.
- SEC-2. Sandboxes hold only short-lived run-scoped tokens; no secrets in
  prompts, specs or logs.
- SEC-3. Treat task text, review comments and CI logs as untrusted: gateway
  injection checks plus a delimited data section in the prompt.
- SEC-4. gVisor isolation, egress limited to code host, package registries
  and gateway; mutual TLS between harness server and control plane.
- SEC-5. Branch protection blocks pushes to default branches; acceptance
  checks and test paths are protected from agent edits.
- SEC-6. Audit every launch, follow-up, cancel and profile change with its
  actor.

## Constraints

Python 3.12, FastAPI, Temporal, Postgres, Redis, Kubernetes Jobs on gVisor
(Docker for development, fake executor for tests), OpenTelemetry.

## Out of scope

Difficulty classification, model selection, fallback and pricing (owned by
ai-api-unified); writing a coding agent; producing tasks; deploying merged
code.

## Open questions

1. Managed sandbox service or Kubernetes, on cost and start latency.
2. Temporal Cloud or self-hosted.
3. Does per-call routing within a band change harness results? Needs an A/B.
4. Run the harness in-process or as a sidecar, for fault isolation.
