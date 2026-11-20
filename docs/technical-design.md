# Meta-Harness Technical Design

## Scope

The meta-harness runs AI coding tasks in parallel. A team sets goals, and each
goal queues tasks from GitHub issues across one or more repos. For each task
the meta-harness picks a harness profile and a model to fit the task's
difficulty, runs them in an isolated per-run sandbox, meters every model call,
and records the outcome, usually a merged pull request. Tasks run
independently, so one task's failure or one model provider's outage leaves
the others running.

### Components

This document is the design. Parts of it are built and parts are planned:
the State column below says which, and the build plan at the end orders the
rest. A section that describes a planned component says so.

| Component | What it does | State | Code |
|---|---|---|---|
| **Meta-harness control plane** | | | `src/meta_harness/` |
| Task service | Accepts and tracks tasks and serves the Task API. Code-host webhooks are planned. | built | `tasks/` |
| Planner | Turns a goal's labeled GitHub issues into tasks with their dependencies, by a periodic sweep, records what becomes of the pull requests of tasks that ended `ready` or `review_unresolved`, and drafts a goal from a label's issues. Planning tasks, the `feature` and `new-system` modalities, issue write-back and Jira and Linear adapters are planned. | partly built | `planner/` |
| Meta-harness scheduler | Admits queued tasks by priority, global concurrency, repo-area locks, closed dependencies and the goal's budget, and carries out cancels and the kill switch, which it owns. Caller quotas and daily caps are planned. | built | `scheduler/` |
| Supervisor | Looks at every open task once a minute, decides who has it (its workflow, the scheduler, its goal's sweep, an operator, or no one), records that, and moves the tasks no one has: a task with no workflow behind it, a parked task whose pull request ended outside any sweep, an unstarted task of an ended goal, a `ready` task of an auto-merge goal, which it sees through its merge, and a task that failed on the system's side, which it retries. Runs in the planner's process. Goal-level stalls and deadlines on operator waits are planned. | partly built | `supervisor/` |
| Meta-harness run service | Runs each task as a Temporal workflow, the RunTask workflow: routes it, runs its candidates, opens its pull request, runs the review loop on it and merges it. Takeover, operator review, exploration and an HTTP API of its own are planned. The only caller of the ai-api-unified router. | partly built | `run_service/` |
| Pool manager | Leases per-run sandboxes by run id, keeps a warm pool, and replaces unhealthy sandboxes. Until it exists, the run activity starts each sandbox itself. | planned | |
| Event service | Holds the ordered record of every meta-harness domain event. | built | `events/` |
| Metering service | Computes cost, time and success per attempt and per task. | planned | |
| Harness analyzer | Proposes changes to router tiers, harness profiles and task policy from metering evidence. | planned | |
| Operator console | The cloud web app operators run the meta-harness from, built on Mantine: goals, tasks, live runs, cancel, the kill switch, and configuration with versions and rollback. Its Inputs, Routing, Pool, Spend and Audit views are planned. | partly built | `console/`, `edge/` |
| Configuration | Versioned router tiers, harness profiles and repo registry, checked on save, served by the planner process | built | `config/` |
| **ai-api-unified library**, in the meta-harness run service process | | | the `ai-api-unified` repo |
| ai-api-unified router | Classifies a task, picks a tier, and returns the tier's ordered harness-profile-and-model candidates and thinking level, building none of them. | built | `AiRouteClassifier` |
| **Per-run sandbox**, one container per run | | built | `images/harness-pi/` |
| Harness server | Serves the harness server Run API and runs the harness in-process. | built | `server/` |
| pi | The default harness: pi's coding agent, third-party (pi.dev), used unmodified. | built | `@earendil-works/pi-coding-agent` |
| pi model client extension | Sends every pi model call to the HTTP Gateway, and holds the owned `submit_review`, `update_plan` and `create_issue` tools. The compaction handler and the owned `run_tests` tool are planned. | partly built | `extension/` |
| **HTTP Gateway** | The only model endpoint a per-run sandbox can reach. Holds the provider API keys, pins each run's model, and enforces each run's spend limit. Its guardrail checks ship off. | built | the `ai-api-unified-http` repo |
| **Outside the system** | | | |
| Code host | Repositories, pull requests, checks, reviews and webhooks. | | |
| Model providers | Cloud model APIs, or local models on vLLM. | | |
| Google Cloud | Infrastructure. Temporal runs on the deployment's VM today; Temporal Cloud is planned. | | |

Per-run code-host access is scoped in the run service itself: a read-only
clone token per run from the GitHub App, and the app's write token only for
Publish. Spend caps live in the HTTP Gateway's run grants and the
scheduler's goal budget.

## Principles

- Configuration, edited in the operator console, picks the harness profiles
  and models a task runs on. The router configuration maps each tier to its
  harness-profile-and-model candidates, and each harness profile defines a
  harness.
- Every AI call goes through ai-api-unified: the router's task classification,
  the planner's goal classification, and every model turn a harness makes,
  which reaches the HTTP Gateway on the run's grant key.
- The system decides by default, and an operator can override any decision
  at any time: a goal's modality, latitude and auto-merge, a plan, and any
  running task, which the operator can take over.
- Only the control plane writes to the code host. A per-run sandbox reads its
  task's repo; the meta-harness run service pushes, opens pull requests, posts
  reviews and merges, and the planner writes issues, so a prompt injection
  inside a run cannot reach the code host.
- A misconfiguration stops a service at startup, before any run: each service
  checks every harness profile and router tier candidate against the images
  and the ai-api-unified configuration, and names the bad setting.
- A task's cost includes every run it took, failed runs included, so the
  harness analyzer compares harness profiles and models on the full cost of a
  completed task.
- Every decision, whether a meta-harness component, ai-api-unified or an
  operator made it, is an audit record in the event service, where the
  operator console reads it.
- Operators and programs control the meta-harness through one REST API, and
  the operator console reaches the meta-harness only through it.

## Architecture

```mermaid
flowchart TB
    OP[Operators]
    CH[Code host and issue trackers]
    PROV[Model providers<br/>cloud or local]
    LS[Cloud Logging log sink<br/>and Pub/Sub topic]

    subgraph CP["Meta-harness control plane"]
        CON[Operator console<br/>owns nothing]
        PL[Planner]
        TS[Task service<br/>Task API]
        SCHED[Meta-harness scheduler]
        RS[Meta-harness run service<br/>RunTask workflows and activities]
        PM[Pool manager]
        EV[Event service]
        MET[Metering service]
        HA[Harness analyzer]

        PLDB[(goal, plan, issue_link,<br/>config_version)]
        TSDB[(task)]
        SCDB[(repo-area locks,<br/>kill switch)]
        RSDB[(run, routing_decision,<br/>operator_review)]
        PMDB[(sandbox, lease,<br/>artifact store)]
        EVDB[(event, schema registry)]
        MEDB[(metering projections)]
        HADB[(proposal)]
    end

    subgraph AIU["ai-api-unified"]
        RT[ai-api-unified router and classifier<br/>library, in-process]
        GW[HTTP Gateway<br/>ai-api-unified-http]
        GWDB[(run grants,<br/>per-run spend counters)]
    end

    subgraph POOL["Per-run sandbox, one per run"]
        HS[Harness server<br/>Run API]
        HX[pi and the pi model client extension]
        HS --> HX
    end

    PL --- PLDB
    TS --- TSDB
    SCHED --- SCDB
    RS --- RSDB
    PM --- PMDB
    EV --- EVDB
    MET --- MEDB
    HA --- HADB
    GW --- GWDB

    OP -->|goals, approvals, cancel, kill switch| CON
    CON -->|create goal, approve, pause| PL
    CON -->|read, tail events| EV
    CON -->|tasks, runs, cancel| TS & RS
    CON -->|kill switch| SCHED
    CON -->|spend| MET
    CH -->|issue, PR, check webhooks| PL & TS
    PL -->|issues: read, create, update, close; pull requests and checks: read| CH
    PL -->|classify goal| RT
    PL -->|create tasks| TS
    SCHED -->|queued tasks| TS
    SCHED -->|start RunTask workflow| RS
    SCHED -->|spend, sandbox capacity| MET & PM
    RS -->|route task| RT
    RT -->|classifier call| PROV
    RS -->|lease sandbox| PM
    RS -->|issue, revoke run grant| GW
    RS -->|run, follow up, events, usage, cancel| HS
    RS -->|push branch, open PR, post review, merge| CH
    HX -->|model turns on the run grant key| GW
    GW --> PROV
    GW -->|ai-api-unified request records| LS
    LS -->|push subscription: gateway_request| EV
    PL & TS & SCHED & RS & PM & HA -->|append events| EV
    MET & HA -->|tail, read events| EV
    HA -->|proposals| CON
    CON -->|configuration edits| PL
```

| Component | Owns |
|---|---|
| Planner | `goal`, `plan`, `issue_link`, `pull_request_end` |
| Configuration, in the planner process | `config_version`, `active` |
| Task service | `task` |
| Meta-harness scheduler | Repo-area locks and the kill switch: Postgres rows today, Redis for the locks later |
| Meta-harness run service | `run`, `routing_decision`, `operator_review`, its Temporal namespace, code-host rate-limit buckets |
| Pool manager | `sandbox`, `lease`, the artifact store |
| Event service | `event`, the schema registry |
| Metering service | Its projection tables |
| Harness analyzer | `proposal` |
| Operator console | Nothing |
| HTTP Gateway | Run grants, per-run spend counters |
| Harness server | The run's pi session directory |

A component reads and writes only its own tables. What it needs from
another component it gets from that component's API or from the event
service, as the scheduler reads a goal's spend from `attempt_finished`
events: no query joins two components' schemas, though today they share one
Postgres.

Configuration is a set of versioned JSON documents, edited in the operator
console's Configuration view: every value a kind's view shows edits in place,
each edit is staged rather than saved, and the staged changes are reviewed
together as a diff against the active version, with a note, before they are
checked and stored as one version. The JSON stays as Advanced. The built kinds are `tiers` (the ai-api-unified
router's `router` section), `profiles` (the harness profiles) and `repos` (the
repo registry). The `config` component, `src/meta_harness/config/`, keeps
every version of each in its `config.config_version` table and the active one
in `config.active`. The planner process serves its API for every kind, until
the run service has an HTTP API of its own. A kind's first version is seeded
from the deployment's file on the first read.

A harness profile added in code reaches the deployment by itself. At startup
the planner compares the shipped `config/harness_profiles.json` with the
history of the `profiles` kind, and adds every profile whose name no version
ever had, as a new version with origin `shipped` and a `config_changed` event.
A profile an operator changed or removed is never touched, because its name is
in the history. An addition the check would refuse is skipped. The task
policy's `max_usd_by_size` reaches it the same way, one size at a time, so a
size an operator changed or removed is never put back. Tiers are not
added this way: they are an ordered routing list, and an added tier would
change how tasks route.

A save is checked the way the service that reads the kind checks its
configuration at startup: the router must parse and name a default tier, every
tier candidate must be a harness pair naming a profile that exists, every
profile needs known tools and positive limits, and a profile a tier still
names cannot be removed. The task policy's `max_parallel` must be a positive
whole number no larger than the sandboxes the VM holds (`MH_MAX_SANDBOXES`),
and each reviewer pair must name a profile that exists. A refused save lists
every reason. A saved version
becomes active at once, is a `config_changed` event with the operator as
actor, and applies to the next task routed; a running task keeps what it
started with. Rolling back saves an earlier version's document again as a new
version, after the same check. With `MH_CONFIG_SOURCE=db`, as the cloud
deployment sets it, the worker, the planner and the scheduler read the active
versions; otherwise they read the files, as the zero-cost end-to-end check
does. The `modalities` and `access` kinds are planned, and an approved
harness analyzer proposal will apply as a new version the same way.

## The harness

A harness is the coding agent a run uses, set by its harness profile's image.
Any image that serves the harness server Run API and honors the run's pinned
model can be one. The default harness is pi's coding agent
(`@earendil-works/pi-coding-agent`). The meta-harness adds to pi:

- the pi model client extension, which registers the only model provider pi
  can see and sends every pi turn to the HTTP Gateway;
- the harness server, which exposes the harness server Run
  API and runs pi's `AgentSession` through pi's SDK.

### Model client

The pi model client extension registers one pi model provider with
`pi.registerProvider()` and its own `streamSimple`. The pi model client
extension needs its own stream function because the HTTP Gateway's
`/v1/conversations/turn` is not a protocol pi implements. The pi model
provider exposes a single pi model, "the run's model", which is whatever
model the HTTP Gateway run grant pins. The pi model has zero cost
metadata, because cost comes from the HTTP Gateway. It declares no
`promptCache` lifetime, so pi never warms the provider prompt cache.

On every pi turn the pi model client extension:

1. Takes pi's transcript context, reading the current system prompt and pi
   tools from pi's transcript helpers, and builds the HTTP Gateway's
   conversation request: system prompt, messages, pi tools, the thinking level
   the run started with, and the `prompt_cache` field set to
   `{"retention": "extended", "key": <harness_session_id>}`. Each earlier
   assistant turn goes back as its HTTP Gateway conversation token. Each pi
   tool result goes as `{"type": "tool_result", "tool_call_id", "result",
   "is_error"}`, the same for every model provider. The HTTP Gateway builds
   the answering model provider's own tool-result message from it
   (ai-api-unified-http 1.19.0), so the pi model client extension holds no
   per-provider message code.
2. Attaches per-call routing hints. pi agent turns carry no route hint, so the
   HTTP Gateway run grant's model policy decides: one pinned model, or a tier
   range, the router tiers from a `route_floor` to a `route_ceiling`. Calls the pi
   model client extension makes itself, for pi session compaction and for
   auxiliary text (commit message, pull request description), carry `route: trivial`.
3. Calls the HTTP Gateway's native conversation endpoint with the run grant
   key. As pi's custom-provider contract requires, the pi model client
   extension calls `options.onPayload` before sending and
   `options.onResponse` on the HTTP Gateway response, awaits
   `options.onProviderStreamEvent` for each pi provider stream event, and
   honors pi's abort signal.
4. Maps the HTTP Gateway turn result into pi's provider event sequence:
   `start`, balanced text and thinking events, tool-call events whose
   arguments the pi model client extension has parsed into input by `toolcall_end`, and exactly one `done` or
   `error`. A tool-use turn arrives from the HTTP Gateway as one response, so
   the pi model client extension emits text once per turn.
5. Finalizes pi's assistant message with the HTTP Gateway's numbers: input,
   output, cached input and cache-write model tokens, the per-turn cost, the
   stop reason, and the model the HTTP Gateway used. The pi model
   client extension adds `cached_input_tokens`, `cache_write_5m_tokens` and
   `cache_write_1h_tokens` from each HTTP Gateway turn's `usage` to the run's
   totals for the harness server Run API's `GET /usage`. The HTTP Gateway
   reports provider prompt cache writes by lifetime because they bill at
   different rates: 1.25 times the input rate for 5 minutes and 2 times for
   1 hour on Anthropic and Bedrock.

Every HTTP Gateway conversation turn sends the whole pi
session history, so provider prompt caching sets most of a turn's cost: a
cached prefix bills at about a tenth of the input rate on Anthropic.

- pi passes `cacheRetention` and `sessionId`
  to the pi model provider on every call. The harness profile image sets
  `PI_CACHE_RETENTION=long`, and the pi model client extension maps `long` to
  the `prompt_cache` retention `extended`: 1 hour on Anthropic and
  Bedrock, 24 hours on OpenAI. A run often waits more than 5 minutes between
  pi turns while tests or builds run, and the default provider prompt cache
  would expire in that gap. `harness_session_id` as the provider prompt cache
  key sends every turn of a pi session to the same OpenAI prompt cache.
- pi's `cacheWarming` setting is `off`, and the pi model
  client extension's pi model declares no `promptCache` lifetime. Otherwise pi
  would send billed provider prompt cache refresh calls through the HTTP
  Gateway.
- The pi model client extension keeps the system
  prompt and pi tool list byte-identical for the whole run: no timestamps, no
  run ids, and a fixed pi tool order. A pi session compaction rewrites the pi
  session history, so the pi turn after it writes a new provider prompt cache
  entry once.
- The HTTP Gateway accepts `prompt_cache` on the
  conversation turn and returns the cached-model-token fields in `usage`.

When an HTTP Gateway guardrail check blocks tool results, the HTTP Gateway
response names their tool-call ids. The pi model client extension replaces
those tool results with a short notice and resends the pi turn. The harness server
emits the `guardrail_blocked` harness server Run API event. When the HTTP
Gateway refuses a turn with `402 run_grant_spend_limit`, the pi model client
extension ends the pi turn with `error`, and the harness server ends the run
`ERROR` with end reason `spend_limit`.

### What pi loads

The harness server creates the pi session with `createAgentSession()` and
decides everything pi loads:

- A custom `ResourceLoader` supplies only the pi model client
  extension, the owned pi extension tools, and the skills the run was sent.
  Nothing loads from the cloned task repo's `.pi/` directory: no pi
  settings, pi extensions, pi skills or `SYSTEM.md`.
- **Skills** come from the control plane. A skill is a directory in the
  usual shape, `SKILL.md` (its frontmatter names it and says what it is
  for) and the files beside it, kept as the `skills` configuration kind
  (`src/meta_harness/config/skills.py`): versioned and rolled back like the
  rest, and available to every goal's runs. A harness profile names the
  skills its runs load (`skills: [...]`, checked against the kind, and
  needing the `read` tool). The run service sends their files with
  `POST /run`; the harness server writes them to a directory of its own
  outside the workspace, so none lands in the repo or the pull request,
  and loads those and no others. pi tells the model each skill's name and
  description and where it is; the model reads the rest when its work
  needs it, so a skill it does not use costs a line of the prompt. A
  skill's scripts run inside the per-run sandbox, under the profile's bash
  allowlist and the egress proxy, so a skill grants no access.
- The kind is seeded from `config/skills/`, and a skill added there later
  reaches a deployment as a new version, as a shipped profile does. It
  ships `no-ai-slop2` (MIT, from codeandtheory/machine-ai-skills, with its
  scanner script), adapted to check the text a run is about to write (a pull
  request summary, a review, an issue draft) rather than a previous reply.
  Every shipped profile that writes for people names it (`pi-lean`,
  `pi-careful`, `pi-review`, `pi-plan`), and their prompts tell the run to
  apply it to its summary, review or drafts before it finishes (What the
  meta-harness writes). A skill or bash command a shipped profile starts
  to name reaches the deployment's profile of that name as a new version,
  once: one an operator removed is not added again. An admin imports a skill on Configuration, Skills,
  from an https git repository on a public host (a branch or tag and a
  path; the commit read is kept) or from a folder on their machine
  (`POST /v1/config/skills/read`, which saves nothing), reads every file
  in full, and saves it as a new version. The import form picks the
  repository from the registered repos, then a branch or tag and a
  directory holding a `SKILL.md` from what the repository lists
  (`GET /v1/config/skills/source`); a name a list lacks is refused, an
  empty branch is the default branch, and a list that cannot be read says
  why and takes a typed value. A skill holds text files only,
  at most 200 files and 2 MB; a skill a profile names cannot be removed.
- pi SDK sessions leave out the pi CLI's built-in `codemode`, `tool_search`
  and MCP extensions, and the harness server keeps them out, because
  `codemode` can run classifier and image models with the session's
  credentials.
- A `ModelRuntime` with no built-in credentials leaves the pi model client
  extension's model as the only one pi can call. The harness
  server never calls `setModel`, so pi never switches models and the HTTP
  Gateway run grant decides the real model.
- pi's SDK `tools` option comes from the harness profile.
- pi loads `AGENTS.md` and `CLAUDE.md` from the pi working
  directory unless pi context loading is off, so the harness profile's
  `context_files` setting decides. The meta-harness run service dispatcher activity puts
  the task repo's `AGENTS.md` in the first pi prompt, so turning
  pi context files on sends it twice.
- `SettingsManager.inMemory(...)` holds these pi settings,
  and pi reads no settings file:

| Setting | Value | Reason |
|---|---|---|
| `compaction.reserveTokens`, `compaction.keepRecentTokens` | The harness profile's `loop.compaction` | Context windows differ by model, so each harness profile sizes its own compaction |
| `retry.enabled` | `false` | pi's default model-call retry (3 retries, backoff up to 60 seconds) would stack on the HTTP Gateway's ai-api-unified fallback chain. A failed pi turn fails the run, and the RunTask workflow's Decide step moves to the next tier candidate. pi's context-overflow recovery (compact, then retry) stays on. |
| `cacheWarming` | `off` | Warming calls would be billed provider prompt cache refreshes |

The HTTP Gateway run grant decides the model, so the harness server registers
no pi virtual models (`registerVirtualModel`).

### Tools

| Tool | Source | What the meta-harness adds |
|---|---|---|
| `read`, `ls`, `find`, `grep` | pi built-in tool | Nothing |
| `write`, `edit` | pi built-in tool | The pi model client extension's tool-call hook refuses protected paths from the task repo policy |
| `bash` | pi built-in tool | A timeout and output cap from the harness profile. The pi model client extension's tool-call hook refuses commands outside the harness profile's allowlist |
| `run_tests` | owned pi extension tool, planned | Runs the acceptance checks named in the task and emits `tests_run` harness server Run API events |
| `submit_review` | owned pi extension tool, meta-harness review harness profiles only | Ends the review run with verdict `clean` or `changes` and its findings, as a `review_submitted` harness server Run API event. The meta-harness run service posts them as a code-host pull request review |
| `update_plan` | owned pi extension tool, offered to every run whose profile has `write` | Records the agent's plan as a checklist (each step pending, in progress or done) in a `plan_updated` event, which the Task page shows as its plan (`features/tasks/Plan.tsx`): the newest one, with a progress bar and a line saying whose it is. While its run is under way it reads as live, with when the agent last updated it and the step in progress marked as going on; with no run under way it is "as the last run left it" and that step is where the work stopped; and a new run that has recorded none yet shows the earlier run's as an earlier run's. A running task with no plan says one may come. The task prompt asks for it on any task of more than a few steps |
| `create_issue` | owned pi extension tool, planning harness profiles only | Records one issue draft as an `issue_drafted` harness server Run API event: `draft_id` (the agent's own short name for it), `repo`, `title`, `body` (which states its acceptance checks), `labels` and `depends_on` (other drafts' ids, or existing issues as `owner/repo#number`). A second call with the same `draft_id` replaces the draft; a draft that depends on itself is refused. It writes nothing to the code host: the planner creates the issues once the plan is approved |

A harness profile's `tools` list chooses from these tools. Until `run_tests`
exists, a run runs its acceptance checks with `bash`. The protected-path
refusal on `write` and `edit` is planned too. Branch protection
blocks default branches. The pull request description holds the run's final
summary, which the harness prompt asks to list the assumptions the run made,
since no person answers questions during a run. When the run leaves no
usable summary, the description holds a fallback the meta-harness writes
instead, under a heading that says so (Publish, below).

### Loop policy

The pi model client extension and the harness server enforce the harness
profile's `loop` settings. Of these, the turn and minute limits are built;
plan-first, `test_before_pr` and the compaction handler are planned, and
until the handler exists pi compacts with its own summary request, on the
run's model:

- The harness server counts pi turns from pi SDK
  events and sends `abort` to the pi session at `max_turns` or `max_minutes`.
- The first pi prompt requires a plan before edits; the pi
  model client extension's tool-call hook refuses writes until pi has emitted
  a plan.
- With `test_before_pr`, the Publish step runs only when
  the `run_tests` pi extension tool has passed in the run's pi session.
- pi starts pi session compaction when the pi
  context passes the model context window minus a reserve
  (`contextTokens > contextWindow - reserveTokens`) and keeps the most recent
  `keepRecentTokens` of pi session history as they are. The harness profile's
  `loop.compaction` sets both (pi's defaults are 16384 and 20000). The pi
  model client extension handles pi's `session_before_compact` pi SDK event:
  the pi model client extension makes the summary call to the HTTP Gateway
  itself, with purpose `compaction`, `route: trivial` and no `prompt_cache`,
  and returns `{compaction: {summary, firstKeptEntryId, tokensBefore, usage}}`.
  pi's own compaction summary request never runs. The compaction summary
  keeps the task spec and acceptance checks verbatim.

### pi sessions

A pi session is one conversation with pi, named by `harness_session_id`.
Today every run starts its own pi session, named after its `run_id`: a
candidate that follows a failed one starts from the task, not from another
model's transcript. With the review loop, the meta-harness run service keeps
the session of the run that opened a pull request, and every meta-harness fix
task on that pull request continues it in a follow-up run. A meta-harness
review run starts its own pi session, so the reviewing model never sees the
authoring model's conversation.

| When | Where the conversation is | Owner |
|---|---|---|
| During a run | pi's session directory in the per-run sandbox, set through pi's SDK `SessionManager` | The per-run sandbox (harness server) |
| Between runs | A copy of that pi session directory in the pool manager's Cloud Storage bucket, stored under `harness_session_id` when the run ends | Pool manager |
| Between runs, briefly | The finished per-run sandbox itself, kept for the pi session for up to `sticky_window` (default 10 minutes) | Pool manager |
| On each model turn | The request the pi model client extension sends to the HTTP Gateway, which forwards it and keeps no copy | Nobody |
| In the event service | Each turn's text and tool calls, as a meta-harness domain event record for the operator console and the metering service | Event service |

A follow-up runs in a per-run sandbox that holds the pi session. Before the
pool manager sandbox lease request returns, the pool manager either hands back
the per-run sandbox it kept for the pi session or copies the pi session
directory from its Cloud Storage bucket into a fresh per-run sandbox. The
harness server then switches pi to that pi session and
continues it, so the pi session conversation keeps its tool history on the
same model.

### Events

The harness server subscribes to the pi session with `session.subscribe()`
and maps pi SDK events (`message_update`, `tool_execution_*`, compaction,
`agent_end`, `agent_settled`) to the typed harness server Run API events, each
with run id, sequence number and timestamp: `turn_started`, `model_call`
(model, thinking, model tokens, cost, latency, as the pi model client
extension recorded them from the HTTP Gateway), `tool_call`, `tool_result`,
`file_changed`, `tests_run`, `guardrail_blocked`, `compacted`,
`review_submitted`, `plan_updated`, `issue_drafted`,
`turn_finished`, `heartbeat`, `run_finished`. The `agent_settled` pi SDK
event marks the end of a pi turn that will not continue on its own. The
harness server then finishes the run or, in a follow-up, waits for the next
input on the harness server Run API.

### Harness profiles

A harness profile sets a harness's image, tools, limits and resources, and is
the harness half of a router candidate. `config/harness_profiles.json` holds
them:

```json
{
  "version": "2026-11-07.1",
  "profiles": {
    "pi-lean":    {"image": null,
                   "tools": ["read", "write", "edit", "ls", "find", "grep", "bash"],
                   "max_turns": 80, "max_minutes": 45,
                   "resources": {"cpu": 2, "memory_gb": 6},
                   "bash_allow": ["cd", "ls", "cat", "grep", "git", "python", "uv", "poetry", "pytest", "ruff", "..."]},
    "pi-careful": {"image": null,
                   "tools": ["read", "write", "edit", "ls", "find", "grep", "bash"],
                   "max_turns": 200, "max_minutes": 120,
                   "resources": {"cpu": 2, "memory_gb": 6},
                   "bash_allow": ["..."]}
  }
}
```

- `image` names the harness profile image for the per-run sandbox. `null`
  uses the deployment's default, `harness-pi`, which carries Node, git,
  Python 3.12, uv and Poetry, so a run can install and test any of the three
  registered repos.
- `tools` lists the pi tools the run may use.
- `max_turns` and `max_minutes` bound the run. The harness server stops pi at
  either, and the run activity may take `max_minutes` plus 10 minutes before
  Temporal retries it.
- `resources` sets the per-run sandbox's CPU and memory.
- `bash_allow` names the programs pi's `bash` tool may run. The harness
  server loads it as an inline pi extension whose `tool_call` handler refuses
  any other program, and any command or process substitution, with a reason
  pi reads as the tool's result. It keeps a run to the tools its task needs;
  the per-run sandbox, not the list, is the security boundary.
- `services` names what the run gets beside its sandbox: `["postgres"]`, or
  `[]` for none. Left out, the machine's `MH_SANDBOX_SERVICES` decides, which
  the VM sets to `postgres`, so every profile gets it without an operator
  rewriting the profiles they keep. With `postgres`, the run service starts a
  Postgres container (`postgres:16-alpine`, or `MH_SANDBOX_POSTGRES_IMAGE`)
  on the run's own network before the sandbox, with its data in memory, one
  CPU and 512 MB, and no port on the host, and waits until it takes
  connections. The sandbox is given its URL as `DATABASE_URL` and
  `MH_DATABASE_URL` (or the names in `MH_SANDBOX_DATABASE_ENV`), and reaches
  it by name, not through the egress proxy. It is removed with the sandbox.
  So a task's tests that need a database run in its sandbox (#200): before,
  this repo's `db` tests could not, every review reported it, and one fix
  run spent 75 turns trying to start a Postgres in `/tmp`.
- The meta-harness run service checks every harness profile at startup: its
  tools, limits, resources, services and allowlist, and every tier
  candidate's profile name.

Planned profile fields, not built: `prompts`, a versioned harness prompt set;
`loop.plan_first` and `loop.test_before_pr`; `loop.compaction`, mapped to pi's
`compaction.reserveTokens` and `compaction.keepRecentTokens`; and
`context_files`. The review harness profile, `pi-review`, has no `write` or
`edit` tool and adds `submit_review`. The planning harness profile,
`pi-plan`, has no `write` or `edit` tool either, adds `create_issue`, and its
Bash allowlist names only programs that read (`ls`, `cat`, `grep`, `find`,
`git` and the like; the allowlist names programs, not subcommands). No run
pushes: only the control plane writes to the code host.

## Routing

Routing happens twice. Once per task, the ai-api-unified router in the
meta-harness run service picks a tier and returns its candidates, each a
harness profile and a model, and the RunTask workflow tries them in order. On
every model call inside a run, the HTTP Gateway routes the call.

When both `standard` candidates fail on infrastructure, the RunTask workflow
escalates to `complex`:

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant WF as Meta-harness run service RunTask workflow
    end
    box rgba(52,168,83,0.14) ai-api-unified library (in the run service process)
    participant RT as ai-api-unified router (in the run service)
    end
    box rgba(251,188,5,0.16) Per-run sandbox (meta-harness image, one per run)
    participant SB as Per-run sandbox: harness server + pi
    end
    box rgba(128,128,128,0.14) External model providers
    participant CL as ai-api-unified router's classifier model
    end

    WF->>RT: run service route activity: classify-only call to the ai-api-unified router (task spec as latest user message, route_floor)
    RT->>CL: classifier model call: classify(task spec)
    CL-->>RT: tier standard, confidence 0.82
    RT-->>WF: tier standard, tier candidates pi-lean with claude-sonnet-5-5, then pi-lean with gpt-5.6-luna
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-lean, claude-sonnet-5-5)
    SB-->>WF: harness server run ended ERROR model_error (provider outage)
    Note over WF: an infrastructure failure, so the RunTask workflow tries the next tier candidate
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-lean, gpt-5.6-luna)
    Note over WF,SB: the pool manager lease fails, no per-run sandbox within ready_timeout
    Note over WF: the standard tier is exhausted and the task policy allows escalation, so the RunTask workflow moves up one tier
    WF->>RT: run service route activity: tier candidates for tier complex
    RT-->>WF: tier candidate pi-careful with claude-opus-5-5
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-careful, claude-opus-5-5)
    SB-->>WF: harness server run FINISHED, pull request opened
    Note over WF: the RunTask workflow records three runs, their costs, route_tier complex, route_method classifier
```

Inside a run, a compaction call goes to a cheap tier:

```mermaid
sequenceDiagram
    autonumber
    box rgba(251,188,5,0.16) Per-run sandbox (meta-harness image, one per run)
    participant PI as pi (coding agent)
    participant EX as pi model client extension
    end
    box rgba(156,39,176,0.12) HTTP Gateway service (ai-api-unified-http)
    participant GW as HTTP Gateway
    participant GR as ai-api-unified router (inside the HTTP Gateway)
    end
    box rgba(128,128,128,0.14) External model providers
    participant PV as Model provider
    end

    Note over PI,GW: HTTP Gateway run grant pins claude:claude-opus-5-5
    PI->>EX: pi agent turn (history, tools)
    EX->>GW: POST /v1/conversations/turn (run grant key, no route hint)
    GW->>GR: send_conversation inside model_pin_scope(claude:claude-opus-5-5)
    Note over GR: pinned: no classifier model call and no tier pick
    GR->>PV: turn on claude-opus-5-5 (that model's own ai-api-unified fallback chain if it fails)
    PV-->>GR: text and tool calls
    GR-->>GW: AITurnResult with cost
    GW-->>EX: turn, usage, HTTP Gateway per-run spend total
    EX-->>PI: text and tool calls
    Note over PI: pi context passes the model context window minus the harness profile's reserve model tokens
    PI->>EX: pi session_before_compact event
    EX->>GW: POST /v1/conversations/turn (run grant key, route trivial, no prompt_cache)
    Note over GW: a route hint turns the run grant pin into a tier range with the pinned tier as ceiling
    GW->>GR: send_conversation(route trivial, route_ceiling complex)
    GR->>PV: compaction summary on the trivial tier's model
    PV-->>GR: compaction summary
    GR-->>GW: result with cost
    GW-->>EX: compaction summary, usage, HTTP Gateway per-run spend total
    EX-->>PI: pi compaction result (summary, firstKeptEntryId, tokensBefore, usage)
```

### Task routing

The meta-harness run service dispatcher activity renders the task spec
(title, description, acceptance checks, repo, file hints, size signals) into
one prompt. The meta-harness run service `route` activity sends that prompt through
`AiRouterCompletions` in classify-only mode. The ai-api-unified router in the
meta-harness run service classifies it, picks a tier, and returns the tier's
candidates, in order.

```json
"router": {
  "tiers": [
    {"name": "standard", "description": "A feature or fix inside one module, with clear acceptance checks, or anything smaller: config, copy, a one-file edit.",
     "models": ["harness:pi-lean/claude:claude-sonnet-5-5", "harness:pi-lean/openai-responses:gpt-5.6-luna"],
     "thinking": "medium"},
    {"name": "complex", "description": "Cross-module changes, migrations, concurrency, security-sensitive code, or vague requirements.",
     "models": ["harness:pi-careful/claude:claude-opus-5-5", "harness:pi-careful/openai-responses:gpt-6-sol"],
     "thinking": "high"}
  ],
  "classifier": {"engine": "openai-decisions", "min_confidence": 0.7},
  "default_tier": "standard",
  "on_exhausted": "escalate"
}
```

The shipped tiers are `standard` and `complex`. The `trivial` tier that the
task policy and the `route: trivial` calls name is planned, with the
compaction handler and the auxiliary calls that would use it. Until a tier
of that name exists, a `trivial` floor is no floor, and a `route: trivial`
call runs on the run's pinned model, as any unknown route does.

A tier candidate takes the form `harness:<harness profile>/<provider>:<model>`.
An OpenAI model is named with the `openai-responses` engine. Every harness
turn sends tools with a reasoning level, and the `openai` engine sends them
through Chat Completions, which OpenAI refuses for its reasoning models. A
save naming `openai` in a tier is refused, and at startup the planner moves
the active tiers' `openai` candidates to `openai-responses` as a new
version, origin `shipped`. A reviewer pair naming `openai` runs on
`openai-responses`. The review loop compares a reviewer with the pull
request's author by model, so one model reached through two engines never
reviews its own work.
The ai-api-unified router reads `harness` as its provider and returns the
string unchanged. In classify-only mode (`AiRouteClassifier`) the router never
builds or probes a candidate, so the meta-harness run service validates each
candidate itself at startup: its harness profile exists and names an image
the executor backend can run, and its `<provider>:<model>` is a model the
ai-api-unified library has configured.

The ai-api-unified router's classifier model reads the latest user message,
so the meta-harness run service dispatcher activity puts the task spec in
that message and keeps instructions in the system prompt.

The meta-harness run service `route` activity builds one ai-api-unified router client per
process and shares it across tasks; the library keeps per-call state in
context variables.

- The ai-api-unified router classifies the task with its classifier model,
  and below `min_confidence` it picks `default_tier`.
- The RunTask workflow tries the tier's candidates in order. A run that ends
  on infrastructure (no per-run sandbox within `ready_timeout`, a rate limit or
  exhausted quota at the model, a lost harness server, or `model_error` from a
  provider outage) moves to the next candidate. A run that ends on a limit
  (`max_turns`, `max_minutes`, `spend_limit`) ends the task.
- When a tier runs out and `on_exhausted` is `escalate`, the RunTask workflow
  calls the `route` activity again with `route_floor` one tier up, within the
  task policy's limits.
- A fix run, an exploration run and a task with a `pin` field
  skip routing and run one named pair, such as
  `harness:pi-careful/claude:claude-opus-5-5`, with no escalation. A fix run
  pins the pair that opened its pull request, so it continues that pull
  request's pi session on the same model. A review run tries the task
  policy's reviewer pairs in order. The meta-harness run service checks a
  pinned model against the ai-api-unified allow and deny lists before any
  lease, and the RunTask workflow records `route_method: model_pin`.
- Each router call sets its own `route_floor` and `route_ceiling`, because
  one router client serves many tasks at once.

Caller hints use the ai-api-unified router's per-call options:

- `complexity: high` on a task passes `route_floor: "complex"`.
- `max_tier: standard` passes `route_ceiling: "standard"`.
- A retry after a failed attempt passes `route_floor` one tier above the
  failed attempt.

The RunTask workflow learns routing only from
the meta-harness run service `route` activity's return value, and cost only from the meta-harness
run service run activity's Collect step, never from ai-api-unified library
events.

| Where | Fields |
|---|---|
| Meta-harness run service `route` activity result | From the ai-api-unified router: `route_tier`, `route_method`, `route_confidence`, `thinking`, the ordered tier candidates, and `classifier_usd` for the ai-api-unified router classifier model call |
| Meta-harness run service run activity Collect result | `run_id`, `sandbox_id`, the harness profile, `end_reason`, `turns`, `elapsed_ms`, the prompt cache hit rate, the run's model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens` and `cache_write_1h_tokens`) from the harness server Run API `GET /usage`, and the dollars the HTTP Gateway counted against the run's HTTP Gateway run grant |
| The task's `max_usd` | The task's spend limit across all its runs, $10.00 when the task names none. Each run's grant gets what the task has left |

The RunTask workflow writes the meta-harness run service `route` activity result as the
meta-harness domain event `task_routed`, and each meta-harness run service run activity
Collect result as one `attempt_finished`. Each run's
`attempt_finished` holds its `run_id`, so it joins the pool manager's
lease record for that run.

### Task policy

The task policy holds the rules for task types, exploration, run budgets,
parallelism and review. It is the `task_policy` configuration kind, seeded
from `config/task_policy.json` and edited on the console's Configuration page.
Built today: `max_parallel`, which the scheduler reads each admission pass,
`review.reviewers` and `review.max_rounds`, which the run service reads, and
`max_usd_by_size`, which the planner reads when it makes an issue a task, and
`writing_pass`, a switch: `false` turns the writing pass off (What the
meta-harness writes), and left out or `true` it is on. Also `max_task_usd`, the
most one task may spend whatever else is set (Budgets).
The other fields below are planned.

```json
{
  "version": "2026-11-03.1",
  "max_parallel": 8,
  "floors_by_task_type": {"security": "complex", "dependency": "trivial"},
  "exploration": {"share": 0.05, "eligible_tiers": ["trivial", "standard"],
                  "challengers": ["harness:pi-careful/openai-compatible:qwen3-coder-next"]},
  "review": {"reviewers": ["harness:pi-review/claude:claude-opus-5-5",
                           "harness:pi-review/openai-responses:gpt-5.6-luna"],
             "max_rounds": 3, "operator_alert_minutes": 240},
  "max_usd_by_size": {"small": "3.00", "medium": "8.00", "large": "20.00"},
  "max_run_minutes": 240
}
```

- `max_parallel` is the most tasks that run at once across every goal. It
  can be no more than `MH_MAX_SANDBOXES`, the sandboxes the deployment's VM
  holds, and a saved change applies at the next admission pass.
- `floors_by_task_type` sets `route_floor` on the router call.
- The RunTask workflow pins a configured share of eligible
  tasks to a challenger harness-profile-and-model pair, so the harness analyzer keeps current data on every harness profile
  and model. Complex and security tasks never explore.
- A run's HTTP Gateway run grant spend limit is what the task has left of
  its `max_usd`, $10.00 when the task names none, after the runs before it.
  So a task's runs together never spend more than its limit. With the review
  loop, a task's `max_usd` also caps the total of its meta-harness review and
  fix runs. `preview_route()` cannot estimate a harness run, because a run's
  turn count is unknown before it starts.
- `max_usd_by_size` gives a task its `max_usd` from its issue's size (Issue
  sizing), when the issue names no limit of its own on a `Spend limit: $N`
  line: an object from `small`, `medium` and `large` to a finite dollar
  amount above 0 and at most the task ceiling (`max_task_usd`, else $100.00),
  since the Task API refuses a task's limit above it. A save that breaks this
  is refused, and a limit read above the ceiling gives none. A size it leaves out, an issue not sized, or a policy without it gives
  the task no limit, so the $10.00 default applies. A change applies to the
  tasks made after it.
- `reviewers` lists the review run's
  harness-profile-and-model pairs in order. The RunTask workflow skips any
  reviewer pair whose model wrote the pull request, so a different model
  always reviews. `max_rounds` caps the fix runs per pull
  request. `operator_alert_minutes` sets when a review round blocked on an
  operator raises an alert. Whether a clean pull request merges is the goal's
  `auto_merge` setting.

### The run loop

The RunTask workflow runs a task's tier candidates in order, one run each. It
checks the budget between candidates, because it holds the
task's remaining spend, its attempts so far and the task policy's limits.

| Step | What happens |
|---|---|
| Identify | `run_id` is the RunTask workflow id and the attempt number, `run-task-<task>:run-<N>`, the same on every retry of the run activity. Each candidate tried, in any tier, takes the next attempt number, so no two runs of a task share an id. A review or fix run takes its task's next attempt number, with tier `review` or `fix`. |
| Lease | Asks the pool manager for an existing pool manager lease on `run_id` first, so a retried run activity re-attaches to a run in progress. Otherwise requests `lease(run_id, image, harness_session_id, resources)` for the harness profile's image. The pool manager response holds the per-run sandbox address, the pool manager lease TTL and the `session_state`: `new`, `kept` or `restored`. In a deployment without the pool manager, the run activity starts one per-run sandbox per run itself, with the Docker executor backend. |
| Grant | Asks the HTTP Gateway for an HTTP Gateway run grant, which the HTTP Gateway issues and stores. The run grant holds the run id, tags, the tier candidate's inner model as a pin, the spend limit, and an expiry at `max_minutes` plus grace. Only the run grant key goes to the per-run sandbox. A retried run activity asks again with the same terms and gets a new run grant key for the live run grant. |
| Start | When `session_state` is `new`, `POST /run` on the per-run sandbox's harness server Run API with the prompt, acceptance checks, harness profile, limits, bash allowlist, the tier's thinking level and run grant key. The pi model client extension sends that thinking level on every turn; a pinned run sends none, so the HTTP Gateway's default applies. When it is `kept` or `restored`, `POST /followup` with the new input and its own run grant key. |
| Wait | Follows the harness server's event stream until `run_finished`, heartbeating the last harness server event id on every event. The run activity's heartbeat timeout catches a dead Temporal worker, and its start-to-close timeout, the harness profile's `max_minutes` plus 10 minutes, is the hard upper bound. A RunTask workflow cancel reaches the run activity through its heartbeat, and the run activity sends `POST /cancel` to the harness server. |
| Collect | Reads the harness server's `/usage` and `/record`, and a finished run's changes from `GET /bundle`. Then revokes the HTTP Gateway run grant and releases the pool manager lease. Returns the terminal state: status, end reason, turns, the files changed, the run's final summary, elapsed time and model token totals. A final summary that is missing, or under three words, gives way to one the run service writes from `/record` (`run_service/summaries.py`): the files, how each acceptance check ended, the last plan and the command counts, under a line that says where it came from. A harness image without `/record` falls back to the files its events named. The terminal state also holds the dollars the HTTP Gateway counted against the run grant. The RunTask workflow records one cost line per run from that figure. |
| Decide | In the RunTask workflow. A run that ended `FINISHED` ends the candidate loop. A run that ended `ERROR` for an infrastructure reason (no per-run sandbox, a lost harness server, `model_error` from a provider outage) tries the next tier candidate. A run whose activity ended because the worker under it shut down (`WorkerShutdown`, which every deploy's restart causes) is neither: the workflow waits 20 seconds for the worker to be back and takes the same run up again, up to five times, and the activity re-attaches to it by its run id, so the restart costs the task no attempt and no candidate. A run that ended on a limit (`max_turns`, `max_minutes`, `spend_limit`) is final. Another candidate would spend the budget again, so only the task policy's retry-up rule or an operator repeats a run that ended on a limit. When every candidate of every tier it may reach fails on infrastructure, as in an HTTP Gateway outage, the task fails with every run listed. Pausing such a task to resume later is not built. |
| Publish | A meta-harness run service activity, after Decide ends the loop on a finished run. Commits the run's changes to the branch `mh/<task>` and pushes it with the meta-harness run service's code-host app token. Opens the pull request, labeled `ai:meta-harness`, with `Closes <issue>` and the run's final summary, which goes through the writing pass first (What the meta-harness writes). A summary that is missing, or under three words, gives way to one the meta-harness writes under its own heading, `Summary written by the meta-harness`, from the files the branch changes, with the run's few words quoted; a summary the run service already wrote from the harness's record is kept as it is. Neither goes through the writing pass (`run_service/pr_summary.py`). Publish records the summary it posted on the task as `pull_request_summary`, once for the opening and once for each fix round, with its source: `run` for the run's own words, `recorded` for the run service's fallback from `/record`, `files` for Publish's own. It appends the event at once, not through the client outbox, so an event service that does not store it fails Publish, and Temporal retries it. A retried Publish that finds the pull request its own earlier attempt opened, as after a failed label request, reads the opening summary back from the description and records that. A fix round's retry adds no second section, and records the section the description holds, whatever its own writing pass returned. The console shows that source on the task's Changes tab, beside the pull request in the task's details, on the task's path and on the goal's board. The description is written when the pull request is opened, and no later push changes it. A retried Publish finds the branch and the pull request it made. A finished run that changed nothing fails the task. A fix run pushes to its pull request's branch. With the Docker executor backend, Publish commits and pushes from the workspace the host mounts. |

The RunTask workflow's Temporal history records every attempt, tier candidate
and decision. The operator console and the harness analyzer read each run's
`attempt_finished` from the event service.

### Model calls inside a run

The harness server's pi model client extension sends every pi model turn to
the HTTP Gateway's native conversation endpoint, with the run grant key as
its bearer credential. The HTTP Gateway:

1. Looks up the HTTP Gateway run grant by its run grant key: the run, harness
   profile, model policy and spend limit.
2. Sets the ai-api-unified observability context: `caller_id` is the
   meta-harness, tags are run, task, harness profile, harness and model.
3. Sends the turn through the ai-api-unified router inside the HTTP Gateway.
   With a pinned run grant model policy (the default) the HTTP Gateway
   wraps the call in the ai-api-unified library's model-pin scope for the
   tier candidate's inner model. The turn skips the classifier model call and
   runs on that model with the ai-api-unified fallback chain, cooldowns,
   guardrails and caching intact. With a tier-range run grant model policy
   the turn sets `route_floor` and `route_ceiling` instead, and the
   ai-api-unified router inside the HTTP Gateway may answer easy turns from a
   cheaper tier. Under either model policy the HTTP Gateway run grant decides
   the provider and model, and the HTTP Gateway ignores any provider or model
   the request body names.
4. Adds the turn's cost to the HTTP Gateway per-run spend counter in Redis,
   so the total is exact across HTTP Gateway replicas, and returns the turn's
   usage and the run's total in the response. Once the run has spent its
   limit, the HTTP Gateway refuses the next turn with
   `402 run_grant_spend_limit`. The pi model client extension reports the
   usage and total, and the harness server emits them in its `model_call`
   events.

A request body with `route`, such as the `route: trivial` on compaction and
auxiliary calls, runs that one call on the named tier, capped at the pinned
model's tier. An unknown route falls back to the pinned model, and pi agent
turns, which carry no `route`, stay pinned. Under a tier-range model policy
the router honors the hint within the tier range.

## Run lifecycle

A task's runs are its first attempt, fallbacks to other candidates and
retries a tier up, each in its own per-run sandbox. Once the task's pull
request is open, its RunTask workflow runs the review loop.

```mermaid
stateDiagram-v2
    [*] --> Queued
    Queued --> Routing: admitted by the meta-harness scheduler
    Routing --> Running: candidate's pool manager sandbox leased
    Running --> Running: fallback to next candidate
    Running --> Checks: harness server run finished, PR opened
    Running --> Failed: tier exhausted, timeout, task budget hit
    Failed --> Routing: retry, one tier up, attempts left
    Failed --> Exhausted: no attempts or task budget left
    Checks --> Reviewing: required checks passed, review run started
    Checks --> Fixing: a required check failed, fix run started
    Reviewing --> Fixing: verdict changes, fix run started
    Reviewing --> Merging: verdict clean
    Reviewing --> Ready: verdict clean, merging off
    Fixing --> Checks: fix run pushed commits
    Merging --> MergeChecks: PR merged, waiting on the merge commit's checks
    Merging --> Fixing: merge conflict, fix run started
    MergeChecks --> Closed: checks passed, or none in 5 minutes
    MergeChecks --> Running: a check failed, fix run on a new branch from the base
    MergeChecks --> Failed: checks pending after 120 minutes, or fix runs or task budget used up
    Checks --> Unresolved: fix runs or task budget used up
    Reviewing --> Unresolved: fix runs or task budget used up
    Closed --> [*]
    Ready --> [*]
    Exhausted --> [*]
    Unresolved --> [*]
```

Each task has one RunTask workflow, whose Temporal workflow id is the task
id, and every run of the task happens inside it. A review or fix run is a
run of that task, inside the same RunTask workflow. `start` uses Temporal's Fail
conflict policy and Reject Duplicate reuse policy, so a task id starts at most
one RunTask workflow, even after it ends.

From admission to close:

1. The meta-harness scheduler grants a slot and calls
   the meta-harness run service's `start`.
2. The guardrails library resolves the target repo's repo policy
   and mints a read-only code-host access token scoped to the target repo, for
   the clone. The meta-harness run service dispatcher activity builds the harness prompt
   from a versioned prompt template: task spec, acceptance checks, the target
   repo's `AGENTS.md`, and the instruction to finish with a summary for the
   pull request.
3. The meta-harness run service `route` activity routes the task with its floors,
   ceilings and exploration pin. The RunTask workflow writes the
   `routing_decision` row, appends `task_routed`, and runs the candidates
   through the run loop, appending one `attempt_finished` per run.
4. The meta-harness run service run activity is the only consumer of its
   sandbox's harness server Run API event stream. It writes the `run` row,
   appends the run's harness server Run API events through the event service
   client library in batches, heartbeats the RunTask workflow, and marks the
   harness run stalled when the harness run misses a heartbeat window.
   Code-host webhooks for the run's pull request (checks completed, merged, closed)
   arrive at the task service, which signals the RunTask workflow.
5. The RunTask workflow runs review and fix runs on the pull request
   until a review comes back clean, then merges it.
6. When the pull request merges or closes, when the meta-harness review loop
   ends `ready` or `review_unresolved`, or when attempts and task budget are
   spent, the RunTask workflow appends the outcome meta-harness domain events
   and sets the task's terminal status through the task service.
7. The operator console sends a cancel to the task service, which records
   the request on the task, and the kill switch to the meta-harness
   scheduler. Each appends the audit meta-harness domain event with the
   operator as actor. On its next admission pass the scheduler cancels the
   RunTask workflow of each task with a cancel request, or of every running
   task while the kill switch is on, and admits none. The kill switch is
   global today; per-run and per-caller scopes are planned. The RunTask
   workflow cancels the meta-harness run service run activity, which calls the harness server Run
   API `POST /cancel` on the per-run sandbox directly, revokes the HTTP
   Gateway run grant, and releases the sandbox lease
   with the pool manager, which destroys the per-run sandbox.
8. Planned: an operator can take over any running task from the operator console. The
   meta-harness run service stops the active run with `POST /cancel`, revokes its run grant,
   and runs the Publish step on the work so far: the branch `mh/<task>` gets
   the run's changes and the pull request opens as a draft, or the existing pull
   request gets the commits. The task becomes `taken_over`, owned by that
   operator, and the RunTask workflow waits on a Temporal signal while the
   operator works the branch. No review, fix or merge runs on a taken-over
   task. The operator ends the takeover from the operator console with hand
   back or close. Hand back starts a follow-up run on the branch, with the operator's notes
   as its input, and the task continues through the review loop. Close ends
   the task with the operator's outcome. Both append audit events with the
   operator as actor.

Every terminal state releases the pool manager sandbox lease. The pool
manager then collects run artifacts into its artifact store and destroys the
per-run sandbox, or keeps it for the same pi session's next follow-up run.

Timeouts come from each harness profile (`max_minutes`), and the task policy
sets the hard ceiling.

### Parallel attempts

A task that has not started can be set to make 2 to 4 attempts at once
(`POST /v1/tasks/{id}/attempts`). Its RunTask workflow routes it, takes that
many of the tier's candidates, and runs one attempt on each in parallel, each
in its own per-run sandbox and workspace, with an equal share of the task's
spend limit. Each is an `attempt_finished`. When more than one finishes, the
task waits for an operator to compare them in the console and pick one; the
pick reaches the workflow as an `operator_message` signal with `when`
`choose`, recorded as `attempt_chosen`. The picked attempt's changes publish
as the pull request, which goes through the review loop as any other; the
others are never pushed. The path is behind the `parallel-attempts` patch.

### Review loop

The RunTask workflow of the task that opened a pull request owns it until it
merges or closes. Review and fix are runs of that task, inside its workflow,
recorded as its attempts with tier `review` or `fix`. The task keeps its
scheduler slot while they run, and its repo-area lock, though once its pull
request is open that lock blocks no other task: the review loop is checks,
reviews and short fix runs, and holding the area through it kept every task
behind it waiting for hours. A conflict a later merge brings is settled at the
task's own merge, which takes in the base first and sends a conflict to a fix
run. Separate review and
fix tasks would add signals between workflows without adding control. The
loop:

1. The RunTask workflow waits for the code host's checks on the pull
   request's head commit. A failed required check goes to step 3 with the
   check's output. Passing checks go to step 2.
2. A review run: the first reviewer pair in the task policy whose model did
   not write the pull request, in a new pi session on the review harness
   profile, which has no edit tools. The review run reads the pull request
   diff, the task's spec and acceptance checks. Its prompt names the code
   host's checks that passed at the reviewed commit, and tells it not to run
   them again: it runs a command only to confirm a finding, or for an
   acceptance check none of them covers (#199). With no checks on the code
   host, it runs the acceptance checks itself. A changed
   file outside the issue's `Paths:` with no reason in the summary is a
   finding. The repo's own versioning rule, in its AGENTS.md, decides about
   its version files whatever the paths say: a version change that rule asks
   for is never a finding, and one it forbids, or a missing one it requires,
   is. A
   review after the first is given the round before's findings and summary
   (prompt `review-v4`): it judges first whether each is fixed, then the fix,
   and raises a new finding only for a bug, a failing check or a part of the
   task left undone, so rounds converge. Its summary begins with how many of
   those findings are fixed, not fixed and new. Every review is shown the
   pull request's description as it stands on the code host (up to 8,000
   characters), since it judges what the description explains, such as why a
   file outside the task's paths changed; an operator's edit to it counts at
   the next round. Its
   `submit_review` tool ends the run with verdict `clean` or `changes` and
   its findings. The meta-harness run service posts them as a pull request
   review and records the verdict with the reviewed head SHA in a
   `pull_request_reviewed` event. The summary and each finding go through
   the writing pass before they are posted; the fix run is given the review
   as the review run wrote it.
   A round after the first can still find something new in code it already
   read, and a loop that keeps doing so never ends. So the run service reads
   which lines changed on the branch since the round before's head
   (`review_follow_ups`, behind the `review-follow-ups` patch), and a new
   finding on code nothing changed since is a follow-up: posted on the pull
   request marked "Follow-up, not blocking", counted apart in
   `pull_request_reviewed` (`follow_ups`, the finding numbers), and not sent
   to a fix run. A finding that names no file, that is within 3 lines of a
   changed line, or that the round before raised too (the same file, within
   15 lines) still holds the merge, and so does every finding when the
   change cannot be read whole. A round whose findings are all follow-ups is
   clean. A fix run's summary is added to the open pull request's
   description as a `Fix round <n>` section, once, so what a fix run is asked
   to explain there (why it changed a file outside the task's paths) is
   where the next review reads it. A fix run that leaves no usable summary
   gets the same fallback as Publish, so its round still shows.
3. On `changes` or a failed check, a fix run: the pair that opened the pull
   request, with the findings or the check output as its input. It continues
   the pull request's pi session in a follow-up run (`POST /followup`), and
   its Publish step pushes to the pull request's branch. The loop returns to
   step 1.
4. On `clean`, the meta-harness run service merges the pull request through
   the code-host API at the reviewed head SHA, so a commit pushed after the
   review never merges unreviewed. A merge conflict with the base branch
   goes to a fix run. When the goal's `auto_merge` is off, or the repo's
   merge policy forbids merging, the RunTask workflow leaves the clean pull
   request open and ends the task `ready`, and the operator console lists it
   for an operator to merge.

The loop stops at the task policy's `max_rounds` fix runs per pull request,
or when the task's runs have spent its `max_usd`. The RunTask workflow then
leaves the pull request open, ends the task `review_unresolved`, and a
meta-harness alert rule fires. A pull request closed on the code host ends
the loop and the task.

An operator can dismiss a finding before its fix run starts (#207, behind
the `review-hold` patch): the first part of the operator review below. When
the task policy's `review.hold_minutes` is above 0, a review that asks for
changes waits that long before its fix run. The task stays `running`, marked
`waiting_on: review`, its status reason saying how many findings wait and
for how long. The operator dismisses any finding that is wrong, with why
(`POST /v1/tasks/{id}/reviews/{round}/findings/{n}/dismiss`, `n` from 1),
and may end the wait at once (`.../reviews/{round}/proceed`); each reaches
the workflow as an operator message. The fix run is asked only for the
findings not dismissed, and the next review is given those as the round
before's. With every finding dismissed, the round counts as clean and goes
on to the merge. The dismissals are posted as a comment on the pull request,
and recorded as `finding_dismissed`; the hold and the word to go on are
`review_hold` and `review_proceeded`. A review no one answers goes to its
fix run with every finding when the wait ends. With `hold_minutes` 0, as it
is unless an operator sets it, findings go straight to the fix run.

A task short of budget holds and asks before it ends (#188, behind the
`budget-hold` patch). Before every run, the first included, the workflow
works out what the run may spend: what the task has left of its `max_usd`,
or, when that is less, the task's share of what its goal has left (the goal's
blocking line, its `max_usd` plus its allowed overage, less what its tasks'
runs have spent, divided among its running tasks, so tasks running side by
side cannot together pass the line). A goal whose budget only warns
(`budget_mode: warn`) caps no run and holds none.
When that is under what the run needs, which is what the task's last run of
that kind spent, and at least $0.10, the task holds: it stays `running`, with
its repo-area lock and its place in the parallel limit, marked
`waiting_on: budget`, its status reason saying what the next run needs and
what is left. A run that stops at its spend limit holds the same way and,
once there is budget, runs again. The operator answers through
`POST /v1/tasks/{id}/budget`: `raise` with a new `max_usd` for the task, or
`end`, which ends the task as it would have ended. An answer that leaves the
next run still short holds the task again, its status reason rewritten with
what is left now (behind the `budget-hold-again` patch), so the hold's reason
and its `budget_request` say what the raise left. A task held for its
goal's budget looks again every five minutes, so raising the goal's budget
on its page lets its tasks go on by themselves, and a budget resume
(`POST /v1/goals/{id}/resume-budget`) tells them to look again at once. A task no one answers in 24
hours ends as if ended. The hold and the answer are recorded as `budget_hold`
and `budget_decision`.

A hold is a request with an amount. Its status reason is a sentence written
and read back by one module (`meta_harness/budget_request.py`), so the Task
API serves the same hold as numbers: a task that waits on budget carries
`budget_request`, null on any other task. For the task's own spend limit it
has `for: task`, the run that cannot start (`what`), what that run needs and
what the task has left of its limit, and the request itself: `add_usd`,
enough for the run in whole dollars and at least $1, and `to_usd`, the limit
that grants it. For the goal's budget it has `for: goal` and the goal's id,
and the amount is the goal's own budget resume's. The Task page's alert reads
"this task asks for $2.00 more" and grants it in one click ("Grant $2.00: its
limit becomes $5.00"), with another limit still to be set and End beside it;
the needs-you list names the item by the amount ("Grant $2.00 more"). A hold
whose reason is not that sentence has no amounts, and the page asks for a
new limit as before.

Two parts are built differently (#85), each until what it waits on exists:

- A fix run starts a new pi session on a fresh clone of the pull request's
  branch, rather than continuing the session that wrote it, because a saved
  pi session cannot be restored into a new sandbox yet. The branch carries
  the earlier work.
- `wait_checks` polls the code host for the head commit's check runs and
  statuses until all complete, rather than waiting for a webhook. A repo with
  no checks five minutes after a push counts as passing.

The review is posted as a comment review, because GitHub refuses an approval
or a change request from the pull request's own author. A reviewer pair whose
harness profile is not active is skipped; with no reviewer left, the task
ends `ready` and unreviewed, with the reason, and never merges.

The merge (#86) reads the goal's `auto_merge` from the planner when it is
due, so a change made while the task runs applies. It squash-merges with
`sha` set to the reviewed head commit. A commit that landed after the review
goes back to the checks and a new review. A pull request that changes a file
under `.github/` or `deploy/` is never merged on a review alone, whatever
`auto_merge` says: a workflow runs with the repo's secrets, and a deploy
applies its Terraform as the project's owner and its scripts as root on the
VM. The task ends `ready`, naming the paths, and an operator's Merge takes it
through. Merges are one at a time per repo:
the run service's merge activity holds the repo's merge lock, a Postgres
advisory lock, from checking the branch to merging it. A branch that lacks the
base's newest commits, because another task's pull request merged while it
was reviewed, is not merged: the base is merged into it through GitHub's
merges endpoint, not `update-branch`, so the merge commit carries provenance
trailers, and the new head goes back to the checks. So no pull request merges against a
base it was not checked on. Whether it is also reviewed again depends on the
pull request's own diff against the base, which the merge activity compares
at the reviewed head and the new one (`same_diff` in its result). When the
diff is the same, as when the base's changes touch none of its files, the
clean review stands: the new head's checks run, and it merges with no new
review (behind the `same-diff-no-review` patch). A merge asked for again
(by an operator or the supervisor) whose base came in the same way merges
on the same terms, with no new review (behind the `resume-standing`
patch). When the diff changed, or
cannot be read whole (300 files or more, or a file with no patch), or the
new head's checks fail, it is reviewed again. A conflict with the base becomes a
fix run: the host merges the base into the fix run's clone, the run resolves
the conflict markers, and its publish commits the merge. Its prompt says
that a conflict in a version or lock file follows the repo's AGENTS.md
versioning rule: the base's version files as they are where a pull request
sets no version, or the base's version with this pull request's bump on top
where it does, and lock files regenerated by their tool. The merged head goes
back to the checks and a new review like any other. A merge asked for again, by an
operator or the supervisor, that meets a conflict starts with this fix run,
before any review (behind the `conflict-first` patch). Branch protection or
any other refusal ends the task `ready` with GitHub's reason.

Nothing merges onto a broken base (#202). Before a merge, the workflow reads
the checks on the base branch's newest commit (`base_health`). While one has
failed, or one still runs, the pull request is not merged: the task waits,
its status reason saying which checks at which commit, and looks again every
minute. A check canceled because a later merge replaced its run is no
failure. When they pass, it merges. When they have not after the task
policy's `merge_wait_minutes` (60 when the policy does not say; 0 merges
without looking), the task ends `ready` with the reason, its pull request
open for an operator to merge once the base is repaired. A fix for a check
that failed after the task's own merge does not wait, since it is what
repairs the base. So merges follow one another at the pace of the base's
checks, its deploy included, and a failed deploy stops the line instead of
carrying every later merge. Behind the `merge-base-health` patch. The merge
commit's message carries a `Version:` line saying what kind of change the
task was, from its type: `feature` for a feature task, `none` for a docs or
test task, `fix` for any other.

In this repo no pull request sets the version (#201). Every pull request
used to bump it in five files, so with tasks in parallel every pair of open
pull requests conflicted there, each conflict was a fix run, and reviewers
had the bump reverted as a change outside the issue's paths. Now the
`VERSION` file holds a base, and the version of a commit is that base bumped
once for each commit on main's first-parent line since `VERSION` last
changed (`src/meta_harness/versioning.py`): a minor bump for a feature, a
patch bump for a fix, none for a change that ships nothing. A commit's kind
is its `Version:` line, else its subject's prefix (`feat/`, `fix/`, or
`chore`, `docs`, `test`, `ci` for none), else a fix. The deploy works the
version out, with the whole history checked out, and gives it to the edge
image as a build argument and to the VM's services in their environment, as
`MH_VERSION`; the version files hold the base and change only with
`VERSION`. A deploy that passes its smoke task publishes its version as a
GitHub release (`v<version>`, on the deployed commit), which the README's
version badge reads. CI fails a pull request that changes a version file. A merged pull
request's `Closes` line closes the issue. The task then waits, still `running`
and holding its repo-area lock, for every check on the merge commit,
deploys included, recorded as `merge_checks`, and its status reason says so
("merged as ...; waiting for the checks on the merge commit"). A check that
did not pass there, canceled because a later merge replaced its run or
failed, is judged by the same check on the base's newest commit that holds
the merge, when there is a newer one: that run covers this merge too, so a
deploy that failed for a reason outside the code (a refused login, a
provider's outage) and passed on the next commit is not this merge's
failure. A canceled check waits for the newer commit's run; a failed one
waits while that commit's checks still run, and stands as failed once they
finish without it. Passing checks, or none within five minutes, end the
task `closed`. A failed check that gave no output starts no fix run, since
a fix run can only change code and has nothing to go on: the task ends
`failed`, naming the check, for an operator. A failed check with output
becomes a fix run from the
base, with the checks' output as untrusted input; a fix run that changes
nothing looks at the merge commit's checks once more, and ends the task
`failed` only if they still fail. Its publish opens a new
pull request on `mh/<task>-merge-<n>` that names the merged one and closes
nothing, and that pull request goes through the review loop, the merge and
this wait again. The rounds count toward `max_rounds` and the runs toward
the task's `max_usd`; when either is used up, or the checks still run after
120 minutes, the task ends `failed` with the checks named. (Behind
`workflow.patched("merge-checks")`: a workflow started before it ends
`closed` at the merge. The status reasons, the no-output rule and the second
look are behind `workflow.patched("merge-checks-judged")`.) The deploy
workflow's update step first logs in to the VM with a command that does
nothing, up to six times, because its SSH key is new each run and the VM
refuses it until the key arrives; the update then starts once.

A task that ends `ready` or `review_unresolved` leaves its pull request open
with no workflow watching it, and its repo-area lock is released. The
planner's sweep watches it instead (#74). For each such task of a goal with a
pull request, the sweep reads the pull request from GitHub with an app token
that reads pull requests, checks and statuses: open, draft, merged or closed,
its head SHA, and its head commit's checks (pending, passed, or failed with
the failed check names). It records each change as an event on the task,
tagged with the goal: `pull_request_checks` once a new head commit's checks
settle, `pull_request_merged`, and `pull_request_closed` for a close without
merging. Each event's id is the one the RunTask workflow uses for the same
fact, so a repeated sweep, or a sweep after the workflow, records it once.
The sweep then reports the end to the task service: a merged pull request
moves the task to `closed`, and one closed unmerged moves it to `canceled`
with that reason. Pending checks are recorded when they settle; a head with
no checks at all is not recorded. The code-host webhook will make the sweep
immediate later.

### Operator review

Operator review is built (#236). An operator always knows when an agent is
reviewing a pull request, and can take the review over.

The notice (`console/src/features/tasks/reviewing.tsx`): a review run with a
`run_prompt` of kind `review` and no `attempt_finished` yet is in progress.
The Overview heads with each pull request being reviewed now, the task's
page shows a banner, and the header inbox lists it with a browser
notification. Each offers Take over this review, and Open the diff, where a
comment on the diff's lines posts to the pull request under the operator's
name.

The takeover:

- `POST /v1/tasks/{id}/review/claim` claims the review of a running task
  with a pull request, at any point of its review loop. The task service
  records the operator on the task (`review_claimed_by`), appends
  `operator_review_claimed` with the operator as actor, and queues the claim
  for the task's workflow. The first claim wins: another operator's is
  refused, naming who holds it.
- The agent's review still finishes and is posted, so the operator starts
  from its findings.
- The round then waits for the operator (behind the `operator-review`
  patch). The task stays `running`, marked `waiting_on: operator_review`,
  its status reason naming the operator, and keeps its repo-area lock. The
  wait is a Temporal condition on a signal, so it survives restarts.
  Nothing releases a claim but the operator.
- `POST /v1/tasks/{id}/review/verdict`, once the round waits: `clean`, or
  `changes` with the agent's findings the operator keeps (`keep`, numbered
  from 1) and their own (`findings`: `body`, with `path` and `line` when
  they have them). The operator's verdict decides the round: the fix run is
  asked for exactly the kept findings and theirs, so an operator can drop an
  agent's wrong finding and can ask for a change the agent did not; with
  none of either the round is clean and goes on to the merge. The workflow
  posts the verdict as a pull request comment that names the operator, and
  the task service appends `operator_review_submitted`.
- `POST /v1/tasks/{id}/review/release` gives the review back, and the round
  decides on the agent's verdict (`operator_review_released`).
- A claim is for one round: the next review is the agent's alone unless it
  is claimed again. A claimed round skips the timed hold of
  `review.hold_minutes`, since the operator's verdict already chooses the
  findings.
- The task's page shows the operator review panel while a review is held
  (`console/src/features/tasks/OperatorReview.tsx`): that the agent's review
  is still running, or the agent's findings to keep or drop, fields for the
  operator's own, Submit: changes needed, Submit: no change needed, and Give
  the review back to the agent. A task waiting for an operator's review
  heads the Overview's Needs you list.

The routes are the task service's, beside the review hold's, not the run
service's: the run service has no HTTP API. Not built: an alert when a
claimed round has waited longer than `review.operator_alert_minutes`, and a
`review_started` event (the `run_prompt` of kind `review` serves as it).

### What the meta-harness writes

Every text the control plane writes for people (a tracker issue or comment, a
pull request's description, a review, a comment on one) leaves through one
pipeline, `src/meta_harness/outbound/`. `outbound.prepare(text, Context)` runs
its stages in a fixed order and returns the text as `Checked`, a string only
the pipeline makes. The adapters that write to the outside (the GitHub, Jira
and own trackers, and the code host's publish, review and comment calls) take
nothing else: `outbound.require` refuses a plain string with how to fix it, so
a write that skips the pipeline fails the first time it runs.

| Stage | What it does | Applies to |
|---|---|---|
| `redact` | Secrets by their issuers' shapes (GitHub, Anthropic, OpenAI, AWS, Google, Slack and Atlassian tokens, JWTs, bearer tokens, private key blocks) become `[redacted]`. First, so no later stage or model sees one | Every text |
| `deslop` | The writing pass below: scan, one rewrite when there are findings, kept only when clean and whole | Text a model wrote (`source: generated`), never a title, an operator's own words (`person`) or the fixed strings in this code (`fixed`) |
| `fit` | Cuts the text to what its destination takes (GitHub 64,000 characters, Jira 31,000, a title 250), and says that it was cut | Every text |
| `stamp` | The provenance line, last, so nothing rewrites or cuts it | A text given a provenance, never a title |

A post composed here from parts (a pull request's description around the run's
summary, a review's body around its findings) sends each model-written part
through as `generated`, then the whole as `fixed`: redacted and cut as a whole,
with no second rewrite. A stage (`outbound/stages.py`) is a small class with
`applies(context)` and `run`; a stage that fails passes its text on unchanged,
unless it is a blocker, whose failure or refusal raises `Blocked` naming the
stage and what to do. No shipped stage blocks: a refused pull request
description would stall its task, so `redact` replaces. What each stage did
rides on the result (`Checked.report`), from which the caller records
`writing_checked`, with `redacted` when secrets were taken out. A new stage is
a class placed in `STAGES`, where its order is its meaning.


Everything the meta-harness posts on a code host for people to read is
checked for the tells of model-written text before it is posted: filler,
puffery, "not X but Y" framing, long sentences, runs of em dashes. It applies
to text written from now on; nothing already posted is changed. Code, diffs,
quoted text, log output and check output are never touched.

It is done three times. First in the prompt: every prompt whose answer a
person reads ends with the same short block of writing rules
(`src/meta_harness/outbound/rules.py`), so the text comes out clean and the
rewrite below rarely runs. That is each run prompt above a summary, a review
or a plan's issues (`repo-task-v1`, `fix-v1`, `review-v4`, `plan-v1`), and
each model call the control plane makes itself through the HTTP Gateway's
`/v1/structured`: a goal's updates, a plan's suggested fixes, a goal's draft
and name, an issue's sizing, a split's parts, a task's name, a failure's
diagnosis and the writing pass's own rewrite. The block is a file of the
writing skill, `WRITING-RULES.md`, named by `config/planner.json`'s
`writing_pass.rules`, so the rules a model is told and the rules the scanner
holds it to are kept together. It is read from the active `skills`
configuration, where an admin may change it, and from the shipped file when
that skill does not hold it or the configuration cannot be read. A test
fails a `/v1/structured` call whose system prompt leaves the block out.

Then by the run: each profile that writes for people
names the `no-ai-slop2` skill, and its prompt tells the run to apply the
skill to its summary, its
review or its issue drafts before it finishes. A task or review run can run
the skill's scanner with `python3`. A planning run cannot: its profile reads
only, so it applies the skill's rules by reading them.

Last by the control plane, in the writing pass
(`src/meta_harness/run_service/writing.py`), which needs no run:

| Text | Where the pass runs |
|---|---|
| The run's summary in a pull request's description | The Publish activity, when the pull request is opened. The lines the meta-harness adds around the summary are not rewritten |
| A review's summary and each finding | The activity that posts the review |
| A comment: findings an operator dismissed, an operator's verdict | The activity that posts the comment |
| The body of an issue a plan drafted | The planner, when the plan is approved |

1. The text goes through the skill's scanner, `no-ai-slop-scan.py`, read
   from the active `skills` configuration and run as a child process with no
   model. Only the hits the scanner decides by itself count as findings; a
   candidate that needs a reader to confirm does not. Neither does a word
   `writing_pass.names` lists: the scanner's word lists hold `harness`, which
   is this product's name, so a text that only names the meta-harness is
   posted with no rewrite.
2. A text with findings gets one rewrite, on the model
   `config/planner.json`'s `writing_pass` names, through the HTTP Gateway's
   `/v1/structured`. A text over 20,000 characters is not rewritten.
3. The rewrite is kept only when the scanner finds nothing in it and it
   keeps every heading, link, code span, code block, number and listed name
   of the original, and every line the control plane reads back: `Paths:`,
   `Depends on:`, `Closes`, and each line under `## Acceptance checks`.
   Otherwise the rewrite is dropped and the original is posted.

The pass never stops a post. A scanner that cannot run, a HTTP Gateway that
does not answer, or a pass that takes over 70 seconds leaves the text as it
was. Each post's pass is recorded as `writing_checked`: on the task, or with
the goal's tag for a plan's issue. It carries the kind of text, how many
texts were looked at, the scanner's findings by category, how many were
rewritten and kept, the reasons a rewrite was dropped, the model and its
cost, and the original and the rewrite of each text that changed. The task's
Timeline shows it as a note on the step it happened in. A rewrite's cost is a model cost of kind
`writing`, of the task or the plan's goal. The task policy's
`writing_pass: false` turns the pass off, with a switch on the Task policy
page; prompts still carry the writing rules, and runs are still told to
apply the skill. `writing_pass`'s settings, and the rules file they name, are
checked when the planner and the run service's worker start.

The pass looks only at model-written text, so the strings this code and the
console write for people are held to the same scanner in CI
(`tests/outbound/test_fixed_strings.py`). It scans every string of three
words or more under `src/` and `console/src`, and a hit the scanner decides
by itself fails the change that wrote it, naming the file, the words and the
fix. Docstrings, SQL, migrations, test files and the strings a model reads (a
prompt, a response schema's descriptions) are left out; the test's
`MODEL_FACING` names those.

Not built: the pass on merge-check notes, which are a task's status reason
and are not posted, and a pull request description updated after a fix run,
since no description is updated today.

#### Provenance

Each thing the meta-harness writes on a code host or a tracker says where it
came from (`src/meta_harness/run_service/provenance.py`). Text for people to
read ends with one line, added after the writing pass so the pass never
rewrites it:

    Provenance: meta-harness 0.49.0; kind review; task t-12; model harness:pi-review/x:y; round 2

The line names the meta-harness version and the kind of post (`pull_request`,
`review`, `comment`, `issue`), then whichever of goal, task, run, model,
round and operator (`by`) are known where the post is written:

| Post | What its line names |
|---|---|
| A pull request's description, and a fix round's section in it | Task, run, model; the fix round |
| A review, and each of its inline comments | Task, the reviewer's model, the round |
| A comment on a pull request | Task; for a line comment from the console, the operator |
| An issue a plan or a split creates | Goal; the planning task; the operator who approved or split |
| A comment on a tracker issue: progress, a reply, a split | Goal; the task, for progress |

A commit the meta-harness makes ends with git trailers instead:
`Meta-Harness-Version`, `-Goal`, `-Task`, `-Run`, `-Model`, `-Round` and
`-By`. That covers a run's commit, a conflict fix's clean merge of its base,
the merge that brings a stale branch's base in before it merges, and the
squash merge, where they follow the `Version:` line. The
`pull_request_opened`, `pull_request_reviewed`, `pull_request_merged` and
`pull_request_commented` events carry the same fields as `provenance`. The
task page's details show the newest of each as a Provenance row. Nothing
already posted is changed. A local repo's merge only moves its base, so it
makes no commit and carries no trailers.

### Sequences

#### A task from admission to its pull request

```mermaid
sequenceDiagram
    autonumber
    box rgba(128,128,128,0.14) Outside the system
    participant C as Caller (Task API client)
    participant CH as Code host
    end
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant TS as Task service
    participant S as Meta-harness scheduler
    participant W as Meta-harness run service: RunTask workflow
    participant A as Meta-harness run service: activities
    participant PM as Pool manager
    participant EV as Event service
    end
    box rgba(52,168,83,0.14) ai-api-unified library (in the run service process)
    participant R as ai-api-unified router (classify only)
    end
    box rgba(251,188,5,0.16) Per-run sandbox (harness profile image, one per run)
    participant SB as Per-run sandbox: harness server + pi coding agent
    participant X as pi model client extension
    end
    box rgba(156,39,176,0.12) HTTP Gateway service (ai-api-unified-http)
    participant GW as HTTP Gateway
    end
    box rgba(128,128,128,0.14) External model providers
    participant P as Model provider
    end

    C->>TS: POST /v1/tasks
    TS->>TS: task row (owned by the task service)
    S->>TS: list queued tasks
    S->>S: admit: concurrency, caps, repo lock
    S->>W: start(task)
    W->>A: dispatcher activity: build harness prompt
    A-->>W: harness prompt
    W->>A: route activity (classify only)
    A->>R: send task spec as latest user message<br/>route_floor / ceiling, exploration pin
    R->>P: classifier model call
    R-->>A: tier and its ordered candidates (harness profile + model each)
    A-->>W: tier, candidates
    W->>W: routing_decision row (owned by the run service)
    W->>EV: append task_routed
    Note over W,PM: first candidate, one run_id per candidate attempt, the run activity's Lease step looks up an existing pool manager sandbox lease first
    W->>A: start the run activity for the first candidate
    Note over A: Lease step
    A->>PM: lease(run_id, harness profile image, harness_session_id, resources)
    PM-->>A: warm sandbox address, per-lease harness server bearer token, session_state new
    Note over A: Grant step
    A->>GW: request HTTP Gateway run grant (run_id, pinned model, spend limit, expiry)
    GW-->>A: run grant key
    Note over A: Start step
    A->>SB: POST /run (harness prompt, checks, harness profile, limits, run grant key)
    Note over A: Wait step
    A->>SB: GET /events (harness server Run API event stream)
    loop each pi turn
        SB->>X: pi turn: transcript, tools
        X->>GW: /v1/conversations/turn, run grant key
        GW->>GW: pin model, HTTP Gateway guardrails
        GW->>P: model call
        P-->>GW: turn result
        GW->>GW: add to HTTP Gateway per-run spend counter (owned)
        GW-->>X: turn, usage, running total
        X-->>SB: pi events: start, text, tool calls, done
        SB->>SB: emit model_call with usage and total, execute tools, emit tool_call, tool_result, tests_run
        SB-->>A: harness server Run API events (SSE)
        A->>EV: append event batch
    end
    Note over A: the run activity heartbeats to Temporal while it follows the harness server Run API event stream
    SB-->>A: run_finished (SSE)
    Note over A: Collect step
    A->>SB: GET /usage
    SB-->>A: usage and reported cost
    A->>SB: GET /bundle
    SB-->>A: the run's changes
    A->>GW: revoke HTTP Gateway run grant (run_id)
    A->>PM: release pool manager sandbox lease
    A-->>W: terminal state FINISHED, end reason, cost
    W->>W: Decide: FINISHED ends the candidate loop
    W->>A: Publish step
    A->>CH: push the branch, open the PR with Closes and the run's summary
    W->>W: run row (owned by the run service)
    W->>EV: append attempt_finished
    Note over W,EV: the meta-harness review loop runs on the PR until a clean review, then merges it
    W->>W: on PR merge or close: outcome
    W->>TS: set status: closed
    TS->>EV: append task_status_changed (closed)
    Note over EV: the metering service tails these meta-harness domain events into task_outcome with dollar-hours
```

#### The operator console opening a run in progress

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant WA as Meta-harness run service: run activity (Wait)
    participant PM as Pool manager
    participant EV as Event service
    participant CON as Operator console
    end
    box rgba(251,188,5,0.16) Per-run sandbox (harness profile image, one per run)
    participant SB as Per-run sandbox: harness server Run API
    end

    PM->>EV: append lease_granted (run_id, sandbox)
    WA->>SB: GET /events (only consumer of the harness server Run API event stream)
    loop each batch of harness server Run API events
        SB-->>WA: harness server Run API events: turn, model_call with usage and running total, tool, test, guardrail
        WA->>EV: append(batch), the event service client library buffers and retries
        EV->>EV: validate per type, dedupe on event_id, assign position, commit, send commit notification
    end
    CON->>EV: read(run_id, from_position 0)
    EV-->>CON: meta-harness domain event history so far, cursor = last position
    CON->>EV: tail(run_id, cursor)
    loop each commit notification inside the event service
        EV-->>CON: new meta-harness domain events after the cursor, in position order
    end
    Note over EV: the event service commit notification is internal, it carries no event data, and tail resumes from any position
    Note over WA,EV: an event service outage buffers in the event service client library, it never blocks the harness server run
```

#### A compaction or auxiliary call inside a run

```mermaid
sequenceDiagram
    autonumber
    box rgba(251,188,5,0.16) Per-run sandbox (harness profile image, one per run)
    participant SB as pi coding agent
    participant X as pi model client extension
    end
    box rgba(156,39,176,0.12) HTTP Gateway service (ai-api-unified-http)
    participant GW as HTTP Gateway
    participant R as ai-api-unified router (in the HTTP Gateway process)
    end
    box rgba(128,128,128,0.14) External model providers
    participant P as Model provider
    end

    SB->>X: pi session_before_compact event
    X->>GW: /v1/conversations/turn, run grant key, route: trivial, no prompt_cache
    GW->>GW: body carries route: pin becomes tier range,<br/>ceiling = HTTP Gateway run grant pinned model's tier
    GW->>R: send with route: trivial
    R->>P: trivial-tier model
    P-->>R: summary
    R-->>GW: turn
    GW-->>X: turn, usage, running total
    X-->>SB: compaction result (summary, firstKeptEntryId, tokensBefore, usage), model_call (purpose: compaction)
    Note over SB: the pi coding agent's own summary request never runs
    Note over SB,GW: pi agent turns carry no route and stay on the HTTP Gateway run grant pinned model
    Note over X,GW: auxiliary calls (commit message, PR description) take the same route trivial path through the HTTP Gateway router
```

#### A run reaching its spend limit

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant W as Meta-harness run service: RunTask workflow
    participant A as Meta-harness run service: activities
    participant PM as Pool manager
    participant EV as Event service
    participant TS as Task service
    end
    box rgba(52,168,83,0.14) ai-api-unified library (in the run service process)
    participant R as ai-api-unified router (classify only)
    end
    box rgba(251,188,5,0.16) Per-run sandbox (harness profile image, one per run)
    participant SB as Per-run sandbox: harness server + pi coding agent
    participant X as pi model client extension
    end
    box rgba(156,39,176,0.12) HTTP Gateway service (ai-api-unified-http)
    participant GW as HTTP Gateway
    end

    X->>GW: /v1/conversations/turn, run grant key
    GW->>GW: HTTP Gateway per-run spend counter (owned) >= run grant spend limit
    GW-->>X: 402 run_grant_spend_limit
    X-->>SB: error
    SB->>SB: harness server stops the run on the limit
    SB-->>A: run_finished: ERROR, end_reason: spend_limit (SSE)
    Note over A: the run activity's Collect step
    A->>SB: GET /usage
    SB-->>A: usage and reported cost
    A->>GW: revoke HTTP Gateway run grant (run_id)
    A->>PM: release pool manager sandbox lease
    A-->>W: terminal state ERROR, end_reason spend_limit, cost
    W->>W: Decide: a run that ends on a limit is final, no next candidate, no escalation
    W->>EV: append attempt_finished (end_reason spend_limit, cost)
    alt task policy retry-up rule applies and attempts are left
        W->>A: route activity, route_floor one tier up
        A->>R: classify only
        R-->>A: next tier and its ordered candidates
        A-->>W: tier, candidates
        Note over W,PM: the candidate loop runs again: Lease, Grant, Start, Wait, Collect, Decide
    else no retry-up rule, or no attempts left
        W->>TS: set status: Failed
    end
```

#### The review loop on an open pull request

```mermaid
sequenceDiagram
    autonumber
    box rgba(128,128,128,0.14) Outside the system
    participant CH as Code host
    end
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant TS as Task service
    participant W as Meta-harness run service: RunTask workflow of the task that opened the PR
    participant A as Meta-harness run service: activities
    participant PM as Pool manager
    participant EV as Event service
    participant CON as Operator console
    end
    box rgba(251,188,5,0.16) Per-run sandboxes (harness profile image, one per run)
    participant SR as Review run sandbox: harness server + pi, pi-review harness profile
    participant SF as Fix run sandbox: harness server + pi, the PR's harness profile and model
    end

    CH->>TS: code-host webhook: required checks passed on the PR head
    TS->>W: signal RunTask workflow: checks passed
    W->>EV: append pull_request_checks (head SHA, passed)
    Note over W: review run: first reviewer pair in the task policy whose model did not write the PR, new pi session
    W->>A: run activity (pi-review harness profile, the reviewer model)
    A->>PM: Lease step: lease(run_id, pi-review image, new harness_session_id)
    Note over A: Grant step: HTTP Gateway run grant for the reviewer model
    A->>SR: Start step: POST /run (PR diff, task spec, acceptance checks, run grant key)
    W->>EV: append review_started (PR URL, head SHA)
    EV-->>CON: tail: review_started
    Note over CON: notification to watching operators, with an offer to review the PR too
    opt an operator claims the review before the agent review run ends
        CON->>W: claim review (operator)
        W->>EV: append operator_review_claimed (actor)
    end
    SR->>SR: read the diff, run the tests
    SR->>SR: submit_review: verdict and findings
    SR-->>A: review_submitted, then run_finished
    A-->>W: verdict changes, findings
    W->>A: post the review
    A->>CH: post the agent's findings as a PR review
    opt the review is claimed
        Note over W: the review round blocks on a Temporal signal until the operator submits or releases
        CON->>W: operator verdict and findings
        W->>A: post the review
        A->>CH: post the operator's findings as a code-host PR review naming the operator
        W->>EV: append operator_review_submitted (actor)
    end
    W->>EV: append pull_request_reviewed (round, verdict changes if the agent or the operator says changes, head SHA), attempt_finished (tier review)
    Note over W: fix run: the pair that opened the PR, with the findings as untrusted data
    W->>A: run activity (the PR's harness profile and model)
    A->>PM: Lease step: lease(run_id, the PR's image, the PR's harness_session_id)
    PM-->>A: sandbox, session_state kept or restored
    Note over A: Grant step: HTTP Gateway run grant pinned to the PR's model
    A->>SF: Start step: POST /followup (findings only, run grant key)
    SF->>SF: switch_session, follow_up, fix, run the tests
    SF-->>A: run_finished
    A-->>W: terminal state FINISHED, the fix run's changes
    W->>A: Publish step
    A->>CH: push the fix to the PR branch
    W->>EV: append attempt_finished (tier fix)
    Note over CH,W: the loop repeats from the next checks webhook, until a clean review on the PR head with passing checks
    W->>A: merge the PR
    A->>CH: merge the PR at the reviewed head SHA
    W->>EV: append pull_request_merged
    W->>TS: set status: closed
```

#### An operator cancel or kill switch

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant CON as Operator console
    participant TS as Task service
    participant S as Meta-harness scheduler
    participant W as Meta-harness run service: RunTask workflow
    participant A as Meta-harness run service: run activity
    participant PM as Pool manager
    participant EV as Event service
    end
    box rgba(251,188,5,0.16) Per-run sandbox (harness profile image, one per run)
    participant SB as Per-run sandbox: harness server + pi coding agent
    end
    box rgba(156,39,176,0.12) HTTP Gateway service (ai-api-unified-http)
    participant GW as HTTP Gateway
    end

    alt cancel one task
        CON->>TS: POST /v1/tasks/{id}/cancel
        TS->>EV: append task_cancel_requested (actor)
    else kill switch
        CON->>S: POST /v1/kill-switch (engaged, reason)
        S->>EV: append kill_switch_changed (actor)
    end
    Note over S: next admission pass: tasks with a cancel request, or every running task while the kill switch is on
    S->>W: cancel the RunTask workflow
    W->>A: cancel the run activity
    A->>SB: POST /cancel (direct, per-lease harness server bearer token)
    SB->>SB: abort, clear_queue, flush pi session
    SB-->>A: status CANCELED (SSE)
    A->>GW: revoke HTTP Gateway run grant (run_id)
    A->>PM: release pool manager sandbox lease
    PM->>SB: drain: collect artifacts and /usage, then destroy
    A-->>W: terminal state canceled, partial cost
    W->>W: run end_reason canceled (owned)
    W->>EV: append attempt_finished with partial cost
    W->>TS: set status: canceled
```

## Harness execution

### Sandbox

Each run gets one per-run sandbox: a pod with the gVisor runtime class in the
`sandbox` namespace.

- The pod runs the harness profile's image, built from a common base with git,
  language toolchains, Node, pi, the pi model client extension and the harness
  server. A harness profile names another image when it needs another
  toolchain or another coding agent.
- The per-run sandbox clones the task's repo at `/workspace` with the run's
  read-only code-host access token. Every per-run sandbox uses that path,
  because a restored pi session needs the working directory it recorded. The meta-harness run service's Publish
  step reads the run's changes through `GET /bundle` and pushes them. With the
  Docker executor backend, the meta-harness run service clones on the host into the mounted
  workspace and publishes from it. The clone's git directory sits beside the
  workspace, not inside it: the per-run sandbox mounts it read-only, and every
  host git command names the git directory and the work tree outright. So a
  hook, a rewritten `.git` file or a `core.fsmonitor` setting that pi writes
  in the workspace never runs on the host when the Publish step commits and
  pushes. A git repository inside the workspace would get around that, since
  git reads a nested repository's own configuration when status or diff
  looks at it: every host git command first refuses a workspace that holds a
  `.git` below its top (the task fails, to be retried), and status and diff
  ignore submodules.
- The image sets the HTTP Gateway URL, and the run grant key arrives in
  the `POST /run` or `POST /followup` body.
- Egress reaches only the code host, package registries and the HTTP Gateway.
- CPU and memory come from the harness profile, 2 vCPU and 4 GB by default.
- The pool manager owns the per-run sandbox. It serves one run at a time, and when the
  run ends the pool manager destroys it, or keeps it for the same pi session's
  next follow-up run.

### Harness server

The harness server is a small TypeScript service in every per-run sandbox. It
serves the harness server Run API to the meta-harness run service run activity and runs pi
in-process through pi's SDK, using `AgentSessionRuntime` for `newSession()`,
`switchSession()` and `fork()`.

| Endpoint | Purpose | pi SDK calls |
|---|---|---|
| `GET /health` | Harness server readiness: pi model client extension registered, the workspace directory exists, HTTP Gateway reachable. A `warm` per-run sandbox passes it before it clones any task repo | pi model runtime state |
| `POST /run` | Start a new pi session for the run. The body holds the prompt, acceptance checks, harness profile, limits, the HTTP Gateway run grant key, and the skills the profile names, each with its files | `newSession()`, `prompt()` |
| `POST /followup` | Continue the pi session the per-run sandbox holds, kept or restored by the pool manager before the pool manager sandbox lease returned. The body holds the new input and the HTTP Gateway run grant key, and no pi session reference. | `switchSession()` for a restored pi session, then `prompt()` |
| `GET /events` | Harness server Run API events as server-sent events, resumable with `Last-Event-ID` | `session.subscribe()`: `message_update`, `tool_execution_*`, pi compaction events, `agent_end`, `agent_settled` |
| `GET /status` | `CREATING`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELED`, `EXPIRED`, plus the last harness server heartbeat event | pi session state |
| `POST /messages` | An operator's message to the run in progress: `next_turn` steers pi, `after_stop` queues it as a follow-up. Records `operator_message_delivered` | `steer()`, `followUp()` |
| `POST /stop` | End the run after the turn in progress, `FINISHED` with end reason `stopped_by_operator`, so its work publishes | `abort()` at the turn's end |
| `POST /cancel` | Stop pi. The pi session file keeps every pi session entry written before the cancel, so a later follow-up run can continue the pi session. | `abort()`, which waits until the pi session is idle |
| `GET /usage` | Model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`), model call counts, and the HTTP Gateway's running total, as the pi model client extension recorded them. These totals serve reconciliation against the run's `model_call` meta-harness domain events | pi model client extension counters |
| `GET /record` | What the run did, for a summary when its own is missing or unusable: the full text of its last turn when that turn called no tool, update_plan's last checklist, the files it wrote or edited, each acceptance check with whether a bash command containing it ran and passed the last time, and how many commands ran and failed | `session.subscribe()`: `turn_end`, `tool_execution_end` |
| `GET /metrics` | Harness server OpenTelemetry metrics: pi turns, pi tool calls, files changed, test runs, elapsed time | harness server counters |
| `GET /bundle` | The run's changes against the commit it cloned, as a git bundle, for the meta-harness run service's Publish step. The Docker executor backend reads the mounted workspace instead | none: git in the workspace |

The harness server waits with `waitForIdle()`, and sends `steer()` or
`followUp()` when input arrives while pi is working. The model comes from the
run grant and the thinking level from the `POST /run` body, so the harness
server calls no `setModel`.

### Executor backends

The pool manager reaches per-run sandboxes through an executor backend. The
run activity calls the harness server Run API directly, over mutual TLS, at
the address and with the bearer token in its lease. Every executor backend
provides these operations:

| Operation | Called by | Purpose |
|---|---|---|
| validate | Pool manager startup | Fail if any harness profile names a harness profile image this pool manager executor backend cannot run |
| provision | Pool manager | Create a per-run sandbox from a harness profile image; return when the harness server `/health` is green |
| destroy | Pool manager | Delete the per-run sandbox and release its resources |
| inventory | Pool manager | List the per-run sandboxes that exist, for pool manager reconciliation |
| address | Pool manager, when it grants a pool manager sandbox lease | The per-run sandbox's pod address and its per-lease harness server bearer token, returned in the pool manager sandbox lease |
| status, usage | Pool manager, on drain | The harness server status and usage endpoints |
| save session | Pool manager, when a meta-harness run ends | Copy the pi session directory to the pool manager's Cloud Storage artifact bucket under `harness_session_id` |
| restore session | Pool manager, before a `restored` pool manager sandbox lease returns | Copy a pi session directory from the pool manager's Cloud Storage artifact bucket into a fresh per-run sandbox, which has the task repo cloned at `/workspace` |
| artifacts | Pool manager, on drain | Harness server logs and the final pi session directory, copied to the pool manager's Cloud Storage artifact bucket |

The backends are Kubernetes pods on GKE, Docker for development and the deployment
without GKE, managed sandbox services, and a fake that replays recorded
Run API event streams for tests.

### Pool manager

The pool manager creates and destroys every per-run sandbox. It is one
leader-elected service that owns the `sandbox` and `lease` tables and the
artifact store, and it reconciles them against the executor backend's actual
sandboxes.

```mermaid
stateDiagram-v2
    [*] --> provisioning
    provisioning --> warm: harness server health green
    provisioning --> unhealthy: harness server ready timeout
    warm --> leased: pool manager sandbox lease granted
    warm --> unhealthy: pool manager health probe failed
    warm --> destroyed: warm TTL or harness profile image rollover
    leased --> kept: run ended, pi session saved, warm-sticky window open
    leased --> draining: pool manager sandbox lease expired, or run ended and not kept
    kept --> leased: follow-up for the same pi session
    kept --> draining: sticky_window passed
    leased --> unhealthy: harness server heartbeats missed
    draining --> destroyed: artifacts collected to the pool manager artifact store
    unhealthy --> destroyed
    destroyed --> [*]
```

A `warm` sandbox has a healthy harness server and no repo, credentials or run.
At lease time the pool manager injects the run's read-only code-host access
token. The run grant key
arrives later, in the `POST /run` or `POST /followup` body. A `kept` sandbox
holds a finished run's repo and pi session for up to `sticky_window`, with its
run grant already revoked. A `draining` sandbox is having its pi session
directory, harness server logs and `/usage` collected. A `destroyed` sandbox
keeps its `sandbox` record for audit.

The run activity requests `lease(run_id, image, harness_session_id,
resources)`, idempotent on `run_id`. The response holds the per-run sandbox address,
the lease TTL and a `session_state`:

- `kept`: the per-run sandbox kept for that pi session is still within its
  `sticky_window`, and the pool manager hands it back.
- `restored`: the artifact store holds a pi session directory for that
  `harness_session_id`. The pool manager copies it into a warm per-run sandbox of that
  image, which clones the repo at `/workspace`, the working directory the pi
  session recorded.
- `new`: the pool manager hands out a warm per-run sandbox of that image, or
  provisions one.

A lease's TTL is the harness profile's `max_minutes` plus a grace period, and
the pool manager destroys the per-run sandbox when it expires, whether or not the run
activity released the lease, so a crashed run activity leaks nothing. The
lease is the only way to a per-run sandbox's address, and the harness server Run API
accepts only that lease's bearer token. When a run ends, the pool manager
saves its pi session directory to the artifact store under
`harness_session_id` before it keeps or drains the per-run sandbox. A follow-up for
the same pi session within `sticky_window` (default 10 minutes) gets the kept
sandbox back and skips the restore.

The pool manager keeps a warm target per image (`warm: 4`), adjusted from the
meta-harness scheduler's queue depth per image. The pool manager replaces a warm per-run sandbox older than `warm_ttl` (default 2
hours), and per-image and global maximums cap the total. The GKE node
autoscaler follows the pool manager's demand.

The pool manager replaces a per-run sandbox whose harness server is not healthy within
`ready_timeout`, and the lease goes to the next warm per-run sandbox. During a run, two
missed harness server heartbeat windows mark the per-run sandbox `unhealthy`; the pool
manager destroys it, and the RunTask workflow's Decide step decides whether
another candidate runs. The pool manager also probes idle warm per-run sandboxes on an
interval. Every 30 seconds it reconciles: it destroys
sandboxes with no record or an expired lease, marks records whose pod has
vanished, and provisions back to each image's warm target.

A per-run sandbox serves one pi session: one run, and the follow-ups `sticky_window`
hands back to it. A fresh per-run sandbox per pi session is the security boundary
between pi sessions, so recycling is drain, destroy and provision a
replacement. When a harness profile changes its image version, the pool
manager stops leasing the old image, replaces its warm per-run sandboxes, and lets
leased per-run sandboxes on it finish. On a node drain, warm per-run sandboxes go at once and
leased ones run until their lease TTL.

The pool manager's metrics: sandboxes by state and image, provisioning time,
time to lease, warm hit rate, health failures by cause, lease TTL expiries,
orphans destroyed by reconciliation, and artifact collection failures.

## Metering

The metering service measures each completed task in dollar-hours:

```
dollar-hours per successfully completed task = Σi (Di × Hi) / Σi Ci
```

| Term | Source |
|---|---|
| Di, dollars for attempt i | The run's `model_call` events, each with the HTTP Gateway's token usage, cost and the run's running total, plus the `route` activity's `classifier_usd` |
| Hi, hours for attempt i | The run's time from `RUNNING` to a terminal state |
| Ci, success for the task | The share of the task's checks that passed: CI checks on its pull request, its acceptance checks, and the merge |

A task's attempts include its review and fix runs, and the metering service
reports the review loop's share apart, from each attempt's tier. Each run's `attempt_finished` event, with the dollars the HTTP
Gateway counted against its run grant, and the harness server's `GET /usage`
cross-check Di, and the metering service flags a gap over 5%. Spend on failed
runs, including every run of a task whose candidates all failed, counts as
the task's wasted spend.

The metering service tails the event service into its own tables:
`model_call`, `attempt_usage`, `check_result`, `task_outcome`, and `spend`
per caller and period.

It also reports the prompt cache hit rate, cached input tokens over input
tokens, per run, harness profile and model. It keeps 5-minute and 1-hour
cache writes apart because they bill at different rates. A low hit rate means
the prompt prefix changes between turns, which multiplies the cost of every
later turn, and the metering service raises an alert.

### Spend controls

- The HTTP Gateway refuses a turn once the run has spent its run grant's
  limit, and the harness server then ends the run `ERROR` with `spend_limit`.
- The harness server ends a run `EXPIRED` at `max_minutes`, and the lease TTL
  destroys the per-run sandbox if the harness server does not.
- The meta-harness scheduler stops admitting a caller's tasks at the caller's daily spend
  cap, and warns at 80%. When the metering service lags, the meta-harness
  scheduler treats spend older than its staleness limit as at cap.
- A hard daily global spend cap and a kill switch stop everything.

## Scheduling

### Admission

The meta-harness scheduler runs an admission pass every few seconds and on
every task event:

1. It orders tasks by priority, then age.
2. It admits a task when global run concurrency is under its limit, the caller
   is under its concurrency quota and daily spend cap, the pool manager has
   sandbox capacity, and no other run holds the task's repo area.
   A task sent back only to merge its reviewed pull request (`resume: merge`)
   takes no area: it writes no code, so it neither waits for nor holds one.
3. A repo-area lock covers the repo plus the top-level directories the task
   names. A task that names none takes no area and runs beside the others:
   each run has its own clone and branch, and the merge step turns a conflict
   into a fix run. Locks live in Redis
   with a TTL, so a meta-harness scheduler leader that dies holding them
   cannot strand a repo.
4. It admits a goal's task once every issue the task depends on is closed,
   while the goal is active, has spent less than its blocking line (its
   `max_usd` plus its allowed overage; a goal whose budget only warns is
   never held for budget), and runs fewer of its tasks than its
   `max_parallel`.

A held area's reason names every task in the way, in a fixed order, so the
same holders always give the same reason.

Every queued task a pass does not start has a reason, and the scheduler
stores it (`scheduler.task_hold`, `scheduler/holds.py`): one row per held
task with the reason, its kind, what it names, when the task began waiting
for it and when a pass last looked. The row goes when the task starts or is
queued no longer. The rows survive a restart, so `task_held` is recorded
only when a task's reason is new or has changed, not again at every deploy.
A task past the system's parallel limit, and every queued task while the
kill switch is on or the scheduler drains, has a hold too. The Task API
serves a queued task's hold as `held`.

As built, the global limit is the task policy's `max_parallel`, 8 as shipped, which the scheduler reads
each pass and checks at startup against `MH_MAX_SANDBOXES`; the locks are expiring
Postgres rows the scheduler renews while a task is `queued`, `running` or
`taken_over`, which covers the task's review loop. An area is a path from
the issue's `Paths:` lines, and two areas conflict when they are the same
path or one is a directory above the other: `console/src/features/goals` and
`console/src/features/tasks` run together, while `console` excludes both. A
task whose issue has no `Paths:` line takes no area, so a repo's tasks run
side by side; its lock row only counts it as running.
A goal's `max_parallel`, when set, holds the goal to that many running
tasks; null inherits the task policy's, and the planner refuses one above
it. The planner, not the scheduler, holds a task `blocked` until its
dependencies close. Caller quotas, spend caps, sandbox capacity and the
modality's default parallelism are planned.

Parallel tasks merge one at a time per repo, each on a head that holds the
current base (Review loop, the merge). The repo's AGENTS.md asks each task to
keep to its paths and to add new files rather than logic to a central list,
and every run's prompt names the paths.

### The supervisor

Every other part of the control plane moves a task when something happens to
it: a workflow step ends, a sweep sees an issue close, an operator presses a
button. Each state has one such mover, and a task whose event never comes
stays where it is: a `running` task whose workflow timed out, a `ready` task
whose merge GitHub refused once, a `blocked` task of a goal no sweep covers.
The supervisor (`src/meta_harness/supervisor/`) works the other way. Once
every `MH_SUPERVISOR_SECONDS` (60; 0 turns it off; checked at startup) it
looks at where every task that has not ended `closed` stands now, and
answers one question for each: who has it?

| Owner | Meaning |
|---|---|
| `workflow` | Its RunTask workflow is running in Temporal, and will move it |
| `scheduler` | It is queued, and an admission pass starts it or records why it holds it |
| `planner` | Its goal's sweep moves it: releases it when its dependencies close, ends it at its pull request |
| `supervisor` | No one has it, and the supervisor's own action moves it |
| `operator` | It waits on a person, and the standing says exactly what for |

There is no sixth answer. `rules.decide` is a pure function of a task's facts
(its status, what it waits on, whether it was asked to cancel, whether the
scheduler started it, its workflow's state in Temporal, its pull request's
state on the code host, its goal's status), and it is total: a test walks
every combination and fails unless each has an owner, an operator is always
told what to do, and no task is left to a driver that is not there (a
workflow that ended, a sweep that does not cover its goal). The same test
compares the rules' statuses with the task service's, so a status added
without a rule fails it. That test, not any one rule, is what keeps a task
from being left with no one.

Each pass saves the answer as the task's standing (`supervisor.standing`:
owner, situation, reason, what an operator is asked, since when, the
supervisor's attempts and its last error), records `task_standing` when it
changes, and the Task API serves it with the task. A task's page shows it as
Who has it. Where the owner is the supervisor, the pass acts:

| Situation | Action |
|---|---|
| A `running` or `taken_over` task, or a `queued` one the scheduler started, whose workflow ended or does not exist | `recover` (`POST /v1/tasks/{id}/recover`): back to `queued`, to resume at its pull request when it has one. The scheduler starts a new workflow. A task asked to cancel is `canceled` instead. After three recoveries it fails, for an operator: something ends its workflows |
| A `ready` or `review_unresolved` task whose pull request merged or closed, of a goal no sweep covers, or of no goal | Ended as a sweep would: `closed` or `canceled`, with the pull request's end recorded |
| A `queued` or `blocked` task of a goal that was canceled or completed | Canceled, with the reason |
| A `ready` task of an active goal with `auto_merge` on, whose pull request the code host would merge, or that conflicts with its base | `merge` (`POST /v1/tasks/{id}/drive`): back to `queued` to resume at the merge. The merge step resolves a conflict in a fix run before any review: reviewed first, a head that cannot merge meets new findings each round and may never reach a clean review, and with it the conflict fix |
| The same, with its head's checks failed | `review`: back through checks, review and fixes |
| The same, with checks that have not reported, or mergeability the code host has not worked out | `wait`: the supervisor has it and looks again each pass. After a day it is an operator's, told to see what holds the checks |
| A `failed` task whose failure was the system's, of an active goal or of none | `retry`: back to `queued` after a wait of 5 minutes, then 30, then 2 hours. It resumes at its pull request when it has one |
| A `review_unresolved` task whose review loop the system stopped, not its findings | `review`: back through the loop after the same waits |

A `ready` task is one whose workflow ended without merging: its goal does
not merge by itself, or the code host refused the merge at that moment (its
required checks had not reported, its base was failing). The first is an
operator's. The second used to be too, forever: the workflow tried once, and
a conflict that appeared later was seen by no one. Now the supervisor reads
how the pull request stands (`supervisor/pulls.py`: `clean`, `conflict`,
`checks_failed`, `checks_pending`, `blocked` or `unknown`, from the pull
request's `mergeable` and `mergeable_state` and its head commit's checks)
and sees the merge through. `blocked` with the checks passed means a rule on
the branch wants what only a person gives, an approval or an exception, and
is an operator's. The supervisor sends one task back at most three times
between an operator's retries (`drives`); after that the task is an
operator's, and its standing says the tries are used up.

A task fails for one of two kinds of reason (`supervisor/failures.py`). The
work could not be done: the run changed nothing, hit its turn limit, a
setting is wrong, the review never came clean. Or the system let it down
while the work was fine: the worker restarted under it in a deploy, a
sandbox did not start, every model was unavailable, the code host answered
5xx. The first kind is an operator's. The second needs only another try, and
used to get none: a task that failed in an outage stayed failed after the
outage ended. The supervisor reads the recorded reason, and calls a failure
the system's only when it names one of a fixed list of errors of the control
plane and what it depends on; a reason it does not recognize, and any that
names the task's own limit, is the task's, so an unknown failure waits for a
person rather than being run again. It retries only a failure less than a
day old: one older is an operator's, told that a retry should now run. The
three tries a task gets are shared with the merges above (`drives`). A
retry after the worker's own restart, which every deploy causes, is not one
of them: the control plane interrupted that run, so the task keeps its
tries, and the spend it carries still bounds it.

A recovery waits until the task has stood with no workflow for three
minutes, so a status write still on its way is not taken for a lost one. A
recovered task's next workflow is given what the task has left of its spend
limit: the task keeps what its runs spent since an operator last retried it
(`spent_carried_usd`), so a recovery never gives a task its limit again, and
neither does a drive. An
action that fails is tried again after a wait that doubles, up to an hour.
Every action is one the task service refuses when it no longer applies, so a
repeat does nothing. What a pass cannot read moves no one: a task whose
workflow Temporal cannot be asked about, or whose pull request the code host
does not answer for, is left as it is, with that as its standing.

A `blocked` task of a swept goal is the sweep's while each issue it waits
on is being worked. It is an operator's, and its standing names the issue,
when one of them is in the way: its task failed, was canceled or ended
unresolved and waits on an operator; it is the task's own issue; or no goal
holds it, so no task will ever close it.

The planner's sweep used to hide its own trouble. A pass over the goals was
wrapped whole with its exceptions dropped, so one goal that raised left every
goal after it unswept, and what a sweep could not do (a repo it could not
list, an issue it could not read) was returned and thrown away. Each goal's
sweep, follow-up and plan collecting now runs by itself: one goal's failure
is recorded on that goal and the next goal still gets its turn. What a goal's
latest sweep could not do is kept, logged when it changes, served with the
goal (`sweep`: when, and the errors) and listed by `GET /v1/supervisor`
(`sweeps_failing`). An issue the planner refused for having no acceptance
checks is read again on every sweep, so adding the checks is all it takes.

The supervisor's own trouble shows: a pass that fails is logged as an error
and said by `GET /v1/supervisor`, which also gives when the last pass ran and
how many tasks each owner has.

Built so far are the actions above and the standing of every task. A run
that the worker's restart interrupts is taken up again by its own workflow
(Decide); a task that fails on a restart all the same is retried by the
supervisor, and that retry is none of its tries.

A RunTask workflow may live 72 hours (`EXECUTION_TIMEOUT`), longer than any
wait inside it. It was six, while a budget hold waits a day and an
operator's review has no limit: Temporal ended those workflows mid-wait,
running none of their code, with the task still `running`. Temporal's limit
is not what notices a dead workflow any more; the supervisor is.

The console's Needs you is the supervisor's record, not a list of its own:
the Overview and the inbox read the tasks whose standing's owner is an
operator (`GET /v1/tasks?has_it=operator`, longest waiting first), each with
what to do and since when. A task of a paused or planning goal is left out:
that hold is the goal's, and shows on the goal. Because the list is the
supervisor's, the Overview says so when the supervisor is off, late or
failing, or a goal's sweep cannot finish: the list may then be missing
something.

A goal has a standing too (`supervisor/goals.py`), read from its tasks'
standings and served with the goal (`standing`: `state`, `reason`, `ask`,
the tasks an operator has, and how many are moving). A goal's status says
what an operator set it to or what the planner found; it does not say
whether the goal is getting anywhere. `moving`: at least one task is being
worked or waits to start with no one's help. `needs_you`: the goal is active
with work left, none of it moving, and an operator has what is left, or its
issues were refused. `held`: the goal itself is paused, planning or stopped
by its budget. `idle` and `done` are the rest. This is what `stalled` was
meant to say and nothing ever set; reading it from the record, rather than
moving the goal to a status its sweep does not cover, keeps a goal that
waits on an operator swept, so it goes on the moment one task is unstuck. A
goal that needs the operator says so at the top of its page, with what each
task needs, in the Overview's list of goals and in the inbox.

An operator removes an issue from a goal that has started with Remove from
goal, on the goal's table of issues: the dialog says what that does to the
issue's task. The goal lets the issue go for good and nothing changes on
the tracker: `issue_link` keeps the row, marked `dropped`, which is what
keeps the goal's label from taking the issue in again. A removed issue is
not listed with the goal, does not count toward the goal being met, and is
free for another goal.

Planned: an open issue whose task was canceled given a new task (#256's
first part, which waits until an operator has had Remove from goal to take
out what should not run again); and a reminder outside the console when a
wait for an operator grows long.

#### Remedies

When the supervisor has a task, `rules.decide` names the situation it is in
and the remedy for it. The choice is deterministic: the same facts always give
the same remedy. Each remedy is a small class in its own module under
`supervisor/remedies/`, listed by id in `remedies/registry.py`. It knows its
most tries (`of`) and how many it has made (`tries`), and `apply` carries it
out through the Task API or the planner. Adding a remedy means writing its
module, listing it, and having `decide` name it for its situation. A test
walks every remedy `decide` can name and fails one with no module.

| Remedy | Situation | What it does | Tries |
|---|---|---|---|
| `recover` | A task with no workflow behind it | Queues it again where its pull request stands, with what it spent carried over; cancels one asked to cancel | The Task API's recovery cap |
| `end_at_pull_request` | A parked task whose pull request merged or closed, which no sweep covers | Closes or cancels it to match | — |
| `merge` | A ready task of an auto-merge goal that the code host would merge, or that conflicts | Sends it back for its merge | The task's drives |
| `review` | A task whose checks failed after its review | Sends it back through the review loop | The task's drives |
| `retry` | A task that failed on the system's side | Runs it again after a wait that grows each try | The task's drives |
| `cancel_for_goal` | An unstarted task of an ended goal | Cancels it | — |
| `wait` | A pull request whose checks have not reported | Nothing this pass | — |
| `retry_stronger` | A task that failed on its own account (it could not finish the work), whose last route was below the complex tier | Runs it again with `complexity: high`, so its route is the complex tier: a stronger model and the careful profile | The task policy's `remedies.retry_stronger` (1) |
| `fix_again` | A review its fix rounds did not settle, on a swept goal | Sends it back through its review loop with `complexity: high`: a fresh set of fix rounds, on the complex tier | The task policy's `remedies.fix_again` (1) |
| `auto_split` (planner) | An open issue of an active goal the sweep held as too large, that no one split, ran anyway or made a task of | After the sweep, drafts the parts (`splits.draft`) and splits it as the Split button would (`split_issue.py`). Keyed in the ledger by the issue's `ref`; a try that fails counts, so it stops at its limit and the issue keeps its Split and Run anyway | The task policy's `remedies.auto_split` (1) |
| `rerun_checks` (run service) | A pull request's head commit whose checks ended with a failed GitHub Actions job (`wait_checks`) | Re-runs the failed jobs of each such workflow run (`POST .../actions/runs/{id}/rerun-failed-jobs`, the GitHub App's Actions write permission) and waits on; one that passes counts as passed, one that fails again goes to its fix run. Stateless: a workflow run already past its attempts is not re-run. Never on a merge commit, where a re-run could deploy an old commit | The task policy's `remedies.rerun_checks` (1), counted by the workflow run's own attempt |
| `raise_limit` | A running task held at its own spend limit (`budget_request` for `task`), whose next run needs at most twice that limit, when its goal's budget has room for the raise | Raises the task's spend limit to what the run needs, as an operator's Raise would. A goal's budget is never raised by a remedy | The task policy's `remedies.raise_limit` (1) |

A remedy with a setting in the task policy's `remedies` (0 to 3 tries; 0 turns
it off; 1 when the policy names none) counts its tries in the ledger, and is
also one of the task's drives, so the Task API's own cap holds too. Once its
tries are used, the task's situation is the operator's again, with the ask it
had before. The console's Task policy view edits both settings.

Every try is one record in the ledger (`supervisor/ledger.py`): a
`supervisor.remedy` row and a `remedy_applied` event, with the same fields.
`situation`, `remedy`, `try`, `of` and `outcome` (`started`, `done` or
`failed`) are for code and tests. `because`, `did` and `next` are plain
sentences for a person, a page or the planner's update, so none of them needs
to read raw events to learn what was done for a task. A try that could not be
applied is recorded `failed`, with its error, and is tried again later.
A plan's draft that names a whole top-level directory on its `Paths:` line
(`tests`, `src`) gets a gap saying every other task touching it would wait for
it, so the planner's suggested fix can narrow it; the planning prompt asks for
the files, or the narrowest directories, from the start.
A failure whose reason names neither kind the rules know (`failures.known`) is
read once by a model (`supervisor/diagnose.py`): one structured call that
answers `transient` or `permanent` with a one-line why, recorded in the ledger
as a `diagnose` row whose `finding` is the kind, keyed by the reason, so the
same failure is not read again. The answer is only the rules' `failure` fact:
the model classifies, and never picks an action. A call that fails leaves the
failure the task's own, as before. Each call is a `failure_diagnosed` event,
counted as planning cost.

The ledger is shown where an operator looks. The task's path notes each try on
the step it started ("Unstuck: ran it again on the complex tier, with a
stronger model (try 1 of 1). Next: …"). The planner's update is given what was
unstuck since the last one (`unstuck` in its facts, and in its digest, so it is
news once) and says it as something done, not asked.
`GET /v1/supervisor/remedies` serves the ledger for a task (`task_id`), a goal
(`goal_id`) or every task, newest first.

### Throughput

| Resource | Scales by | Notes |
|---|---|---|
| Per-run sandboxes | Warm targets and maximums per image | The GKE node autoscaler follows the pool manager's demand; a managed sandbox service can add a second pool |
| HTTP Gateway | Replicas | All model traffic goes through it, so it has its own SLO and canary deploy |
| Model providers | ai-api-unified fallback chains per model | Fallback and cooldowns spread load across providers |
| Control plane | Stateless replicas and Temporal workers | One run activity per active run |
| Code host API | The GitHub App's installation and webhooks | Conditional requests, no polling |

Per run, the control plane's own load is one router classification, about two
short completions, and the run's event stream. A harness's model turns go straight
from the per-run sandbox to the HTTP Gateway.

## REST API

Every REST API path starts with `/v1` and enters through the external HTTPS
load balancer, which routes it to the service that owns it.

In the deployment that exists, the edge does this work. It signs operators
in with Google, admits the identities on the access list
(`MH_CONSOLE_OPERATORS`, the platform stack's `operators` variable), and
holds the session in a signed cookie. It forwards each `/v1` request to the
service that owns the path's prefix, from its `ROUTES` table, with the
service key and the operator's identity in `X-MH-Actor`, so a new path
prefix needs a `ROUTES` entry. Every operator may read every path. The
admins, `MH_CONSOLE_ADMINS` (the platform stack's `admins`, a subset of the
operators), alone may write configuration, use the kill switch and open the
`/v1/admin` paths, which the edge serves itself; while the list is empty,
every operator is an admin. An admin who is not an operator stops the edge
at startup.

Those two lists are only the first access list. The users are records the
planner keeps (`planner.console_user`, `planner/users.py`), and an admin
changes them on the console's Users page: invite an email as an admin or a
regular user, set a role, deprovision. The edge reads the users every ten
seconds and decides each request by them (`edge/directory.py`), so a
deprovisioned user's session ends at their next request and a new role
applies at once. The first time the edge finds no users, it hands the
planner its first access list (every operator an admin when
`MH_CONSOLE_ADMINS` is empty), and from then on the users decide. A planner
the edge cannot reach changes nothing: the users last read go on deciding.
An invited user becomes active at their first sign-in. A sign-in the edge
cannot report to the planner, as during a deploy, is reported after its next
read of the users that reaches the planner. The console sends no
email: an invitation is text the admin copies and sends. The rest of this
section is the design for the GKE deployment.

Identity-Aware Proxy authenticates every `/v1` path except the webhooks.
Operators sign in through Identity Platform, and programs present
a Google service account's token. Each service accepts traffic only from the
load balancer and checks the IAP signed header on every request. The access list, a configuration document,
maps each verified identity to a role: an operator may call every path, and a
caller may submit tasks and read its own tasks and runs. It also gives each
caller its concurrency quota and daily spend cap. Every write
records an audit event with that identity as actor. Locally, with no IAP,
each service takes bearer keys from its environment instead.

Each webhook path skips IAP, and its owning service checks the code host's or
tracker's signature on every delivery. Services call one another with a
Google-signed ID token for the target service, and each service accepts only
the service accounts listed for it. A per-run sandbox holds no REST API
credential.

| Method | Path | Owner | Purpose | State |
|---|---|---|---|---|
| POST | `/v1/tasks` | Task service | Submit a task, idempotent on `external_ref`. Returns the task id. A task names at least one acceptance check, except a `plan` or `smoke` task and a goal's task a service submits: the planner's for a sized issue with no checks, which runs with the gap shown | built |
| GET | `/v1/tasks` | Task service | Tasks by status, goal or `external_ref`, or by who has them (`has_it`: `operator`, `supervisor`, `workflow`, `scheduler` or `planner`, from the supervisor's standing, longest in their situation first); a caller sees its own | built |
| GET | `/v1/tasks/{id}` | Task service | State, current run, routing decision, pull request URL and outcome. `held` says why the scheduler holds a queued task: `{reason, kind, since, last_checked, detail}`, null for a queued task it does not hold and for a task in any other status. `kind` is one of `goal_budget`, `goal_status`, `goal_parallel`, `system_parallel`, `repo_area`, `kill_switch`, `draining` or `start_failed`, so a page need not read the reason's words, and `detail` names what the reason is about (`goal_id`, the tasks holding the places as `holders`, the shared `paths`). The list (`GET /v1/tasks`) carries `held` on each queued task too. The task service reads it from the scheduler's record with scheduler code (`scheduler/holds.py`) | built |
| POST | `/v1/tasks/{id}/cancel` | Task service | Cancel a task that has not started, or ask for its active run to stop, which the scheduler carries out | built |
| GET | `/v1/pulls/{task_id}/diff` | Meta-harness run service, served from the planner process | The files the task's pull request changes, with their added and removed counts and patches, and its head commit. The Task page's Changes tab | built |
| GET | `/v1/pulls/{task_id}/checks` | Meta-harness run service, served from the planner process | The code host's checks on the commit the task waits on, its pull request's head or its merge commit once merged (`of`): each check's state, times and log, with how long it took on the default branch's newest commit (`usual_seconds`), and how they stand together. Read only, for the Task page's Checks tab | built |
| POST | `/v1/pulls/{task_id}/comments` | Meta-harness run service, served from the planner process | A review comment on lines of the pull request's head commit, posted as the GitHub App with the operator named in its text, and recorded as `pull_request_commented` with the operator as actor | built |
| POST | `/v1/tasks/{id}/attempts` | Task service | Run a task that has not started as 2 to 4 attempts at once | built |
| GET | `/v1/pulls/{task_id}/attempts` | Meta-harness run service, served from the planner process | A task's attempts side by side: candidate, how each ended, cost, and the changes in each one's workspace, and whether the task waits for a pick | built |
| POST | `/v1/tasks/{id}/messages` | Task service | An operator's message to a running task (`when`: `next_turn`, `after_stop`), or `stop` after the current turn, recorded as `operator_message`. The scheduler hands each to the task's RunTask workflow as an `operator_message` signal, and the workflow's `deliver_message` activity gives it to the harness server of the run in progress (behind the `operator-messages` patch) | built |
| GET | `/v1/tasks/{id}/reviews` | Task service | The task's review rounds, oldest first: verdict, reviewer, reviewed commit, summary, findings (each numbered from 1, with who dismissed it and why when an operator did) and review URL, from the `review_submitted`, `pull_request_reviewed` and `finding_dismissed` events, each with what came next, read from the events before the next round: its own fix run, the merge, another review when the head changed before it could merge, waiting, or the loop's end. The Task page's Reviews tab | built |
| POST | `/v1/tasks/{id}/reviews/{round}/findings/{n}/dismiss`, `/v1/tasks/{id}/reviews/{round}/proceed` | Task service | An operator's word on a review its task holds for them (`waiting_on: review`): dismiss finding `n` (from 1) with a `reason`, or end the wait now. 409 unless the task holds a review. The scheduler hands each to the task's workflow | built |
| POST | `/v1/tasks/{id}/budget` | Task service | An operator's answer to a running task held for budget (`waiting_on: budget`): `{"action": "raise", "max_usd": ...}` saves the task's new spend limit (above the old one; left out, the task only looks at its budget again) and `{"action": "end"}` ends it. The scheduler hands the answer to the task's workflow. `GET /v1/tasks?waiting=true` lists the tasks waiting on an operator, each held for budget with its `budget_request`: what it asks for, as numbers | built |
| GET | `/v1/tasks/{id}/spend` | Task service | Where the task's money went: each of its runs, oldest first, with its kind (the task's own run, review or fix), candidate, turns, model calls, input, cached-input and output tokens, cost, and how it ended, from its `attempt_finished` and `run_finished` events, and their totals in all and by kind against the task's `max_usd`. A run's input tokens are every token its model read: the HTTP Gateway's `input_tokens` plus the run's cache writes, which that count leaves out, so the cached share is of the whole prompt. The totals also give `writing_usd`, what the writing pass's rewrites of the task's posts cost (its `writing_checked` events), which no spend limit counts, and `all_usd`, the runs' cost plus that: the task's figure on the Costs page. The Task page's Spend tab | built |
| GET | `/v1/goals/{id}/spend` | Planner | Where the goal's money went: each of its tasks with what its runs spent in all and by kind of run, and its runs and turns, most expensive first, and the goal's total and its split by kind against its budget. Opened from the Goal page's spend line | built |
| POST | `/v1/tasks/{id}/retry` | Task service | Send a `review_unresolved`, `failed` or `canceled` task back to `queued`, with `max_usd` as its new spend limit when given; its `retries` counts up and `task_retried` is recorded. The scheduler starts a new RunTask workflow for it (the one case a task id runs a second workflow, after the first has ended). With an open pull request the workflow resumes at the review loop, on the pull request's head, with fix rounds and spend counted from nothing and its runs numbered after the earlier ones; a pull request that has merged ends the task `closed`; with none, or one closed unmerged, it makes a new first run | built |
| POST | `/v1/tasks/{id}/merge` | Task service | Ask for a `ready` or `review_unresolved` task's pull request to be merged: back to `queued` with `resume: merge`, and its new workflow merges through the merge queue whatever the goal's `auto_merge` says, then waits for the merge commit's checks. A head that moved goes through checks, review and fixes first. A conflict goes first to the fix run that resolves it, then through checks and review. Either way the operator's request stands: once those come back clean it merges, whatever `auto_merge` says | built |
| POST | `/v1/tasks/{id}/clear` | Task service | Take a `ready` or `review_unresolved` task off the list: `closed` when its pull request's merge is recorded, else `canceled`, with the reason naming the pull request and who cleared it. The pull request stays on GitHub | built |
| POST | `/v1/tasks/{id}/status` | Task service | Set a task's status. Services only: the RunTask workflow and the planner | built |
| POST | `/v1/tasks/{id}/recover` | Task service | The supervisor's move for a `running` task, or a `queued` one the scheduler started, with no RunTask workflow behind it (`why`, and `spent_usd`, what its runs have spent): back to `queued` to resume at its pull request, `canceled` when it was asked to cancel, or `failed` after three recoveries. Services only. 409 for a task in any other status | built |
| POST | `/v1/tasks/{id}/drive` | Task service | The supervisor's move for a `ready` or `review_unresolved` task it can move on (`action` `merge` or `review`, `why`, `spent_usd`), or for a `failed` one whose failure was the system's (`retry`): back to `queued`, with its spend carried. Services only. 409 for a task in any other status, a merge or review with no pull request, or a task already sent back three times | built |
| GET | `/v1/supervisor` | Planner | The supervisor's last pass: when it ran, how many tasks each owner has, what it recovered, ended and canceled, what it could not do, and how often it runs | built |
| GET | `/v1/supervisor/remedies` | Planner | What the supervisor did to unstick tasks, newest first, for a task (`task_id`), a goal (`goal_id`) or all: each try's situation, remedy, try of how many, outcome, and why, what it did and what comes next as sentences | built |
| POST | `/v1/tasks/{id}/take-over` | Meta-harness run service | Stop the active run, publish its work so far, and hand the task to the calling operator | planned |
| POST | `/v1/tasks/{id}/hand-back`, `/close` | Meta-harness run service | End a takeover: a follow-up run continues on the branch, or the task closes with the operator's outcome | planned |
| GET | `/v1/runs/{id}` | Meta-harness run service | Run state, candidate, token usage and cost so far | planned |
| GET | `/v1/routing` | Meta-harness run service | Routing decisions and the candidate each task finished on, by modality, tier and time | planned |
| GET, POST | `/v1/kill-switch` | Meta-harness scheduler | Read the kill switch, or turn it on or off with a reason. While it is on, the scheduler stops every running task and admits none. `mode` sets it instead: `stop` is on, `drain` admits nothing new and lets running tasks finish (a deploy that replaces the VM drains), and `off` ends both. `kill_switch_changed` carries the mode. Scopes smaller than everything (a run, a caller) are planned | built |
| POST | `/v1/tasks/{id}/review/claim`, `/verdict`, `/release` | Task service | An operator review: take the review of a running task's pull request over, give the verdict the round then waits for (`clean`, or `changes` with the agent's findings kept and the operator's own), or give the review back. 409 for a task with no review to take, a review another operator holds, or a verdict before the agent's review has finished | built |
| GET, POST | `/v1/goals` | Planner | List goals, or create one from `statement`, `repos` and `label`; the planner sets the rest. Each listed goal carries `cost_usd`, what its tasks' runs spent, the same total as `/v1/goals/{id}/spend` (`planner/goal_costs.py`, one read of every task and every `attempt_finished` for the whole list, kept 30 seconds); null with `cost_unknown` saying why when the Task API or event service did not answer, never 0. The goal row's own `spent_usd` is written only by a budget check, so it is not the goal's cost. `start` (default `true`) creates it `active` and sweeps it at once; `start: false` saves it `paused`, not started (`started_at` null), and takes in no issue until its first `/resume` | built |
| GET, PATCH | `/v1/goals/{id}` | Planner | The goal with its issues, plan or phases, criteria and spend (`cost_usd` as on the list: the one figure the console shows for a goal, on its page, its budget alert and dialogs, and the Overview; the board's per-task costs come from `/v1/goals/{id}/spend`), or change its modality, latitude, `auto_merge`, priority or budget. Built: the goal with its issues, and changes to its title, statement, `label`, `repos`, `auto_merge`, priority, budget (`max_usd`, null for none), `budget_mode`, `budget_overage_percent`, `max_parallel` (null to inherit) and `strict_issues`, each sent alone by the Goal page's in-place edits and recorded as `goal_changed` with the fields changed. A repo must be registered; `label` may be null only when the goal holds issues an operator picked by ref (`picked` on its issues; ones its label swept in do not count) in its repos, else 409. Tasks the goal already made keep their issues, and its next sweep takes in issues by the new label and repos; an issue it holds with no task yet in a repo it no longer names is let go, recorded as `issue_dropped`, and never becomes a task there | partly built |
| GET | `/v1/repos` | Planner | The repo registry: the repos a goal may name | built |
| GET | `/v1/repos/available` | Planner | The repos the deployment's GitHub Apps are installed on that the registry does not list yet, and each app's page for installing it on more. Configuration, Repositories adds from it | built |
| GET | `/v1/labels` | Planner | Every label on the registered repos' open issues (or one repo's, with `repo`), with its open-issue count in total and per repo, and how many of those issues open goals hold (`held`, `held_by`). The New goal form's Label field suggests from it | built |
| GET | `/v1/repos/{owner}/{name}/issues` | Planner | A registered repo's open issues, newest first, with a `label` and text `q` in the title when given, a page at a time from `cursor`, each with the open goal that holds it, if any | built |
| POST | `/v1/goals/draft` | Planner | A goal a model drafts from the open issues of one label (`label`) or of several (`labels`), each issue read once, or from the `refs` chosen: title, statement, theme and modality, through the HTTP Gateway with `config/planner.json`'s `goal_drafting`. A goal takes one label, so a draft from several answers `label` null and the issues it read in `refs`, which the New goal form sets as the goal's picked issues. Saves nothing | built |
| POST | `/v1/goals/{id}/pause`, `/resume`, `/cancel` | Planner | Pause, resume or cancel the goal. Resuming a goal not started starts it: it sets `started_at` and sweeps the goal at once. Cancel also cancels the goal's `queued` and `blocked` tasks and asks its `running` tasks with no pull request to stop; the goal comes back with `tasks`: the task ids `canceled`, `stopping`, `left` (a pull request open) and `errors` | built |
| POST | `/v1/goals/{id}/resume-budget` | Planner | Raise the budget of a goal that is over it or stopped by it, so it can go on: by `add_usd`, or with no body by a worked-out amount (what it has spent, plus what its issues not yet done are likely to cost, rounded up to a whole dollar). Makes a `budget_exhausted` goal `active` and tells its running tasks held for the goal's budget to look again at once. Answers `{goal, from, to, spent_usd, how, tasks_told}`. 409 for a goal with no budget, one that only warns, one within its budget, or an `add_usd` that leaves it stopped | built |
| POST | `/v1/goals/{id}/sweep` | Planner | Sweep an active goal's label now, rather than at the next interval; a goal stopped by its budget may be swept too | built |
| POST | `/v1/goals/{id}/complete`, `/reopen` | Planner | Set an `active`, `paused` or `stalled` goal `met` by hand, or move a `met` or `canceled` goal back to `active`, which sweeps its label at once | built |
| GET | `/v1/goals/{id}/issues` | Planner | Each of the goal's issues with its `title` (as its last sweep read it; null before one has), its sizing (`size`, `size_reasons`, `missing`, `split`, its `paths` and `checks_named`; null and empty for an issue not sized), why it is held (`held_reason`) and what it was split into (`split_into`), its task's status, what the task waits on (`task_waiting_on`), pull request URL, pull request state (`open`, `merged` or `closed`) and checks: the newest recorded head commit's `passed` or `failed` with the failed check names, or `pending` while an open pull request has none recorded, and whose words its description's opening summary is (`summary_source`: `run`, or `recorded` or `files` for a fallback the meta-harness wrote; null when none was recorded) with how many fix rounds' summaries the meta-harness wrote (`fix_summaries_written`). The goal's board shows the two apart: a badge for the opening summary's source, and one for those fix rounds. The task's facts come from the Task API and the `pull_request_*` events on it. With them, the goal's status (`goal_status`), and for a goal no sweep covers, when its open issues' states were last read from the tracker (`states_checked_at`, at most a minute old) and whether every one could be read (`states_complete`); `GET /v1/goals/{id}` reads them the same way | built |
| GET, PUT | `/v1/goals/{id}/plan` | Planner | GET: the goal's newest plan version (`version`, `drafts`, its decisions on the goal's issues in `items`, those `issues`, its `summary` and `gaps`, `invalid` reasons, `source` planned or edited, `created`: the issue each draft became, `tracked`: where an approval put them). PUT takes `drafts` and, when given, `items`, the planning task still drafting if one is (`planning_task_id`), and the label its issues carry. PUT: an operator's edited `drafts`, saved as the next version, valid or not. Only for a goal in `planning` with no planning task in flight | built |
| POST | `/v1/goals/{id}/approve`, `/replan` | Planner | Approve (`track`, default true: whether the new tasks become issues in the goal's tracker or are kept in the meta-harness): apply the newest plan, settling each decision on the goal's issues, and create its new tasks' issues, in dependency order, with the goal's label, hold them for the goal, and make it `active`. Each draft's body goes through the writing pass before it becomes an issue. An invalid plan is refused (409) with its reasons; a refusal by GitHub (502, with GitHub's reason) leaves the goal in `planning`, and approving again carries on from the next draft, making none twice. Replan, with an optional `note`: start a new planning task, given the note and the plan it replaces. Approving a phase is planned | built |
| POST, DELETE | `/v1/goals/{id}/issues`, `/v1/goals/{id}/issues/{ref}` | Planner | Add issues to the goal by ref, or remove one from it, with or without a task. A ref another goal holds is refused, naming that goal. Removing an issue cancels its task when the task has not ended, and answers `{dropped, task_canceled}`; a task with a pull request open is refused (409) unless `force=true`, which cancels the task and leaves the pull request open. The goal keeps the ref marked `dropped`, so its label never sweeps the issue back in; adding it back by ref clears the mark, and another goal may take it meanwhile | built |
| POST | `/v1/goals/{id}/issues/run-anyway` | Planner | Make an issue the sweep held (`ref`) a task whatever its size and gaps, at the next sweep, which an active goal runs at once. 404 for an issue the goal does not hold or removed, 409 for one not held, closed (the route reads the issue from GitHub first and records it closed; 502 when it cannot be read), or a met or canceled goal. Records `issue_released` | built |
| POST | `/v1/goals/{id}/issues/split` | Planner | Create the issues a held one (`ref`) splits into, from `issues` (`title`, `body`; 1 to 20), in its repo with the goal's label; a goal with no label holds them by ref. Each body must have acceptance checks and a `Paths:` line (422 names each part and what it lacks), and gets a `Split from` line; the original gets a comment naming its parts and closes once they are done; a refusal by GitHub is a 502 naming the issues created before it, which are recorded as the split. The held issue stays held, saying what it was split into, across sweeps; one split already is refused (409), as is a closed one; one the goal removed is a 404, checked as for run-anyway. Records `issue_split` | built |
| GET | `/v1/activity` | Planner | Every pull request the meta-harness opened in the last `days` (91), oldest first, for the Overview's Pull requests row: each one's state (in review, waiting, blocked, merged, closed; merged or closed as its `pull_request_merged` and `pull_request_closed` events say, so a canceled task's pull request left open is waiting, not closed), when it was opened and reached that state, its fix rounds and latest run; the newest 500, with how many older ones are left out. No cost: that is `/v1/costs`'s. From the Task API and the tasks' events | built |
| GET | `/v1/inputs` | Planner | Queued issues across every registered repo, by repo and by goal, with each one's state | planned |
| GET | `/v1/config` | Configuration, in the planner process | Every kind with its active version | built |
| GET | `/v1/config/models` | Configuration, in the planner process | The models a tier candidate can name, as `<engine>:<model>`: every priced completions model in ai-api-unified's model registry, OpenAI's through `openai-responses`. A tier candidate's model select offers them | built |
| GET | `/v1/config/images` | Configuration, in the planner process | The harness images the deployment builds, which a harness profile's image is picked from: the one image `make images` (`mh-images.service`) builds, `HARNESS_IMAGE` from `HARNESS_DIR`, and `default`, the image a profile with no image runs. Another directory under `images/` is not listed, since nothing builds it. 422 says why when the build directory has no readable Dockerfile (`config/images.py`) | built |
| GET | `/v1/config/images/programs` | Configuration, in the planner process | The programs a harness image has (`?image=`, empty for the default), which a harness profile's bash allowlist is picked from: every executable file on the image's PATH and bash's builtins, listed by a container of the image with no network and no capabilities, and kept per image id. Only an image the deployment builds is run. 422 says why when they cannot be listed (`config/programs.py`) | built |
| GET | `/v1/config/classifiers` | Configuration, in the planner process | The engines the router's classifier can name, each with its models: the engines ai-api-unified's provider registry lists for classification, and `none`; each engine's models from its model registry (`openai-decisions` its Decisions models, the others their provider's priced completions models, `openai-compatible` none, so its model is typed). The tiers view picks the classifier's engine and model from them, and refuses a name the list lacks | built |
| GET | `/v1/config/{kind}` | Configuration, in the planner process | The active version of `tiers`, `profiles`, `repos` or `task_policy`; `modalities` and `access` are planned | built |
| GET | `/v1/config/{kind}/versions` | Configuration, in the planner process | The document's version history | built |
| PUT | `/v1/config/{kind}` | Configuration, in the planner process | Save a new version, checked as at startup; 422 lists every reason a bad one is refused. A `repos` version that drops a repo an open goal names is refused, naming the goal | built |
| POST | `/v1/config/{kind}/rollback` | Configuration, in the planner process | Make an earlier version's document active again, as a new version | built |
| GET | `/v1/proposals` | Harness analyzer | Pending and decided proposals with their evidence | planned |
| POST | `/v1/proposals/{id}/approve`, `/reject` | Harness analyzer | Decide a proposal; an approved one applies as a new configuration version | planned |
| GET, POST | `/v1/events` | Event service | Events by run, task, type or source after a position, paged; services append with POST | built |
| GET | `/v1/events/tail` | Event service | The same filter as server-sent events, resumable from any position | built |
| GET | `/v1/spend` | Metering service | Spend and dollar-hours grouped by goal, modality, repo, model, harness profile or tier, over a time range | planned |
| GET | `/v1/pool` | Pool manager | Per-run sandboxes by image and state, warm targets and lease latency | planned |
| POST | `/v1/webhooks/code-host`, `/v1/webhooks/{tracker}` | Task service, planner | Code-host and issue-tracker webhooks, each checked by its signature | planned |
| GET | `/v1/limits` | Planner | The task policy's `max_parallel`, which a goal inherits and may lower, and `max_sandboxes`, the VM's ceiling on it | built |
| GET | `/v1/search` | Planner | Goals whose title, statement, label or id hold `q`, and tasks whose title, id or issue ref do, through the Task API's `q`. The console's command palette | built |
| GET, PUT | `/v1/me/settings` | Planner | The signed-in user's own settings: planner defaults for New goal (repos, modality, latitude, budget, auto-merge), appearance and browser notifications. A user reads and writes only their own row, keyed by the identity the edge names | built |
| GET | `/v1/version` | Edge | The version and the deployed commit (`MH_COMMIT`, which the release stack sets), shown in the console's header | built |
| GET | `/v1/me` | Edge | The signed-in operator's identity and roles (`operator`, and `admin` for an admin) | built |
| GET | `/v1/admin/logs` | Edge | The VM's services' and the edge's log entries from Cloud Logging, newest first, at most 500, by unit, lowest severity, time and text. Every entry has a message: a service's line is its text; a request Cloud Run served, which has none, reads as its method, path, status and time (and counts as an error at 500 or above); an audit entry as its method and status; any other structured entry as its fields. An empty line a service printed is left out. The VM's Ops Agent ships the journal as `mh_journald`; the edge reads with its own service account's `roles/logging.viewer`. Admins only | built |
| GET | `/v1/admin/deploys` | Edge | What is deployed: the version, the commit the VM and the edge run, and the edge's revision. Admins only | built |
| GET, POST | `/v1/admin/users` | Planner, through the edge | The console's users: list them, or invite an email with a role (`admin` or `user`). `PATCH /v1/admin/users/{email}` sets the role and `DELETE` deprovisions. The last admin can be neither deprovisioned nor made a regular user (409). Each change is recorded (`user_invited`, `user_role_changed`, `user_deprovisioned`) with the admin as actor and shows on the Audit page. Admins only | built |
| GET | `/v1/costs` | Cost service, in the planner's process | Costs over time: `from`, `to`, `bucket` (`day`, `week`, `month`), `group_by` (`source`, `kind`, `goal`, `task`, `repo`, `model`, `engine`, `operator`, `label`, `outcome`) and a comma-separated filter on each. `label` is the labels of the issue a task worked on, and `outcome` what became of its pull request (`merged`, `closed` without merging, still `open`, or `none` opened), as its `pull_request_merged` and `pull_request_closed` events record it, not the task's status, so a canceled task's pull request left open is `open` until it merges or closes, which the planner's sweep, or the hourly follow-up of a goal no sweep covers, records then: a task's facts, not a cost row's, which the planner gives when costs are asked for, so a relabel or a merge shows at once. A task whose issue carries several labels puts an even share of its cost under each in a breakdown by label, so the series add up to the total. `choices` offers the labels and outcomes of the tasks that spent. Answers the buckets, one series per value most expensive first (the eighth and later folded into `other`), each bucket's total, the total, the daily average, the change against the same span before, the least-squares `trend` through the totals, a month projected at the trend, the ten costliest tasks, and what each source is. An admin sees every source and goal; anyone else, only the model cost of their own work (`scope: own`). `POST /v1/costs/harvest` reads every source now (admins) | built |
| GET | `/health` | Every service | Liveness and configuration identity | built |

The run service has no HTTP API yet. Until it has one, a path it owns that
needs the GitHub App, such as a pull request's diff, is served from the
planner process, as the configuration API is, by run service code.

| Task field | Required | Meaning |
|---|---|---|
| `external_ref` | yes | The caller's own id, unique per caller |
| `repo` | yes | The repository URL |
| `task_type` | yes | `bug`, `feature`, `refactor`, `dependency`, `test`, `security`, `docs`, or `plan` for a planning task. `review` and `fix` are reserved: no caller may submit them, and review and fix are runs of their task |
| `parent_task_id` | no | Reserved. No task has a parent today |
| `priority` | yes | Higher runs first |
| `title`, `description` | yes | The task text, treated as untrusted |
| `acceptance_checks` | yes | Commands or check names that define success. At least one, except for a `plan` or `smoke` task, which may send an empty list: a planning task's result is its issue drafts, and a smoke task passes by ending closed |
| `paths` | no | The paths the task touches, which scope its repo-area lock |
| `complexity` | no | `low`, `medium` or `high`, which becomes `route_floor` |
| `max_tier` | no | A tier name, which becomes `route_ceiling` |
| `max_usd` | no | The task's spend cap, which caps each run's grant |
| `pin` | no | One candidate, such as `harness:pi-careful/claude:claude-opus-5-5`, run with no routing or escalation |
| `goal_id` | no | The task's goal. The planner sets it |
| `depends_on` | no | The `external_ref`s this task waits for |

A unique index on `(caller,
external_ref)` deduplicates submissions and serves the `external_ref` lookup.

### Goals

A goal holds the work toward one outcome: its tasks come from GitHub issues
across one or more registered repos. Goals run side by side and compete for run
capacity by priority.

| Field | Meaning |
|---|---|
| `title` | The goal's name in every list and header, at most 80 characters. The operator writes it, a goal drafted from a label takes the drafted one, and otherwise the planner's goal drafting model writes it from the statement after the goal is created (`goal_titled`), and at startup for any goal without one. Until then, the console shows the statement's start |
| `statement` | What the goal is, in prose; the console calls it the goal's description. The planning task reads it |
| `repos` | The registered repos the goal may change |
| `label` | The GitHub issue label that queues an issue for this goal, in any of its repos. Optional: a goal also holds the issues an operator picks by ref (`POST /v1/goals/{id}/issues`, or `refs` when it is created), and its sweep takes those in beside its label's. A goal needs a label or picked issues, unless it takes the issues assigned to the meta-harness in its tracker |
| `modality` | `fix`, `feature` or `new-system`, which picks the goal's rules. The planner classifies it when it is unset |
| `latitude` | 0 to 1: how far the planner may go beyond what the goal names. The modality's default when unset |
| `success_criteria` | Checks the planner evaluates to decide the goal is met: commands against a repo, an issue query such as "no open issues with the label", or a check name. A goal that names none is met when every issue it holds is closed and none of its tasks is queued, blocked or running |
| `auto_merge` | Whether a pull request whose review comes back clean merges without an operator. The modality's default when unset |
| `max_usd`, `priority` | One budget and one priority for all the goal's tasks, review and fix runs included |
| `budget_mode` | What the budget does when the goal's runs have spent it: `block` (the default) stops the goal at its blocking line, and `warn` lets it keep going, with the budget reached recorded and shown and nothing held or refused. It has no effect on a goal with no budget |
| `budget_overage_percent` | How far past its budget a `block` goal may go before it stops: a whole number, 0 or more, 10 by default. The goal blocks at `max_usd × (1 + overage / 100)`, and between the budget and that line it runs on, over budget. 0 blocks at the budget exactly. Ignored in `warn` mode |
| `max_parallel` | The most of the goal's tasks that run at once. Null inherits the task policy's `max_parallel`; a goal may set it lower, never higher. The New goal form sets it, and the Goal page shows the value in use, marked inherited or set, and edits it in place. A change applies at the next admission pass |
| `strict_issues` | Whether an issue missing acceptance checks, paths or a testable outcome is held instead of run (Issue sizing). False by default: such an issue runs, with the gap on its card. A change applies at the next sweep to the issues not yet made tasks |
| `status` | `planning`, `active`, `paused`, `met`, `stalled`, `budget_exhausted` or `canceled` |

A `feature` or `new-system` goal starts in `planning`. For every active goal the planner runs a loop:

1. Each open issue with the goal's label in one of its repos is sized (Issue
   sizing) and becomes a task, with `external_ref`
   `github:<owner>/<repo>#<number>`, unless its size or its gaps hold it for
   an operator. The planner refuses an issue in another repo and comments why.
2. The RunTask workflow routes and runs each task within the modality's
   routing floor and ceiling.
3. The review loop reviews each pull request to the modality's bar, merges it
   and so closes its issue.
4. When the goal's issues are closed, or a phase ends, the planner evaluates
   the success criteria.
5. If they pass, the goal is `met`. If work is still queued, the loop goes on.
   If nothing is queued and the criteria fail, a `fix` goal waits for
   issues, and a `feature` or `new-system` goal gets a planning task for its
   next step.

A goal's statuses move this way. `planning` becomes `active` when its plan
is approved. `active` becomes `paused` and back by an operator, `met` when
its criteria pass or an operator completes it (a `paused` or `stalled` goal
can be completed too), `stalled` when every open
issue is blocked by a failed issue or waits on an operator, and
`budget_exhausted` when a `block` goal's spend reaches its blocking line
(never for a `warn` goal). `stalled` and
`budget_exhausted` return to `active` once an operator unblocks the goal or
raises its budget: the next sweep finds the line above what is spent, and a
budget resume does it at once. Any open goal can be canceled. Canceling a
goal ends what it has not started: each of its `queued` and `blocked` tasks
is canceled with the reason "its goal was canceled", recorded as the task's
`task_status_changed`, and each `running` task with no pull request is asked
to stop, as a task cancel does. A task with a pull request open is left for
the operator, who merges it, closes it or cancels the task. Pausing a goal
cancels nothing: its tasks wait and resume with it. An active goal's sweep
sends a canceled task of an issue that is still open, and still the goal's,
back to work (`retaken` in its result): its task is retried, resuming at its
pull request when one is open, against the spend limit it had. So reopening
a canceled goal brings its open issues' tasks back. An operator who wants an
issue left alone removes it from the goal (Remove from goal), and the goal
never takes it in again. A failed task is not retaken: the supervisor
retries a failure that was the system's, and any other is an operator's. A
task whose issue was closed is not retaken either. A `met` or `canceled`
goal takes no more issues until an operator reopens it, which makes it
`active` again, so a `fix` goal over a label that keeps receiving issues is
reopened, or given a criterion that never passes. A goal can also be saved
without starting (`start: false`): it is created `paused` with `started_at`
null, which tells it from a goal paused after running, and takes no issue
until its first resume. That resume sets `started_at`, records
`goal_changed` with the status change, and runs the goal's first sweep at
once, as creating an active goal does. `goal_created` records `start`.

As built, every goal is a `fix` goal with a label and is created `active`,
or `paused` and not started when it is saved without starting.
With no success criteria yet, a goal is met by the rule for one that names
none: on each sweep, which is also when the planner sees that a task's merge
closed an issue, an `active` goal that has held at least one issue, whose
issues are all closed, whose label has no open issue it could take in, and
none of whose tasks is queued, blocked, running, taken over, ready or
unresolved, becomes `met`, and the planner emits `goal_met`. A goal that never
held an issue waits for its first one, and a paused goal, including one saved
without starting, is not checked. An operator completes a goal with Complete
goal and reopens a `met` or `canceled` one with Reopen goal; a reopened goal
whose label has no open issue is met again on its next sweep. Every move is a
`goal_changed` event, the goal's history. Nothing sets `stalled` yet.
`success_criteria`, the modality classifier and latitude are planned.

The planner classifies a goal statement's modality with the ai-api-unified classifier's `classify_choice`,
the call the router uses to pick a tier, reading each modality's
`description`. At or above `classifier.min_confidence` it sets the modality;
below it, or on a refusal, it sets `feature` and lists the goal as "modality
uncertain" in the operator console, where an operator confirms or changes it without
holding the goal up. The classifier also sets an unset latitude, starting from
the modality's default and moving it up for a statement that asks for
open-ended improvement and down for one that names an exact change. The planner
records each classification as `goal_classified` with its confidence, so the
harness analyzer can measure how often operators override it. An operator can
change the modality or the latitude at any time; a new modality applies from
the next planning task.

#### Issue sizing

An issue that is too big for one task spends its whole limit and ends
unresolved: one issue became 2,100 changed lines across 22 files. So the
sweep sizes each issue the first time it sees one, before it becomes a task
(`planner/sizing.py`). One model call through the HTTP Gateway's
`/v1/structured`, with a completions engine the gateway serves (not the router
classifier's `openai-decisions`, which it refuses there) and the model
`config/planner.json`'s `issue_sizing` names (`engine` `none` turns sizing
off), reads the issue's title and body as data and answers its `size`
(`small`, `medium` or `large`), its `reasons`, what it is `missing`
(`acceptance_checks`, `paths`, `testable_outcome`) and, for a large one, the
titles of the issues it would `split` into. What the planner sees without the
model, no `## Acceptance checks` or no `Paths:` line, is added to `missing`.
The answer is stored on the issue link (`size`, `sizing`) and recorded as
`issue_sized` with the model and its cost.

An issue opened by someone with no write access to the repo (GitHub's
`author_association` is not OWNER, MEMBER or COLLABORATOR) is held before it
is sized: anyone may open an issue, and its text becomes an agent's
instructions. Its `held_reason` names the author and says to read the issue,
its raw text too, then Run anyway; an issue an operator picked by ref is not
held. Every task's title and description leave out HTML comments, which
GitHub hides from whoever labeled the issue, and any line shaped like the
prompt's `--- task ---` markers (`tracker.task_text`).

After sizing:

- A `large` issue is held: it does not become a task, and its link's
  `held_reason` says why, with the suggested split. The goal's board shows it
  under Needs attention with two actions. Run anyway
  (`POST /v1/goals/{id}/issues/run-anyway`, `issue_released`) makes it a task
  at the next sweep, which an active goal runs at once. Split asks the planner
  for the parts (`POST /v1/goals/{id}/issues/split-drafts`, one model call,
  `split_drafted`, a planning cost; `planner/splits.py`): each a complete issue
  saying what to change, the outcome a reviewer checks, its paths on a `Paths:`
  line and its acceptance checks, to edit, drop or add to. When the planner
  cannot draft them, the dialog says why and starts from an outline of the
  suggested split. Creating them (`POST /v1/goals/{id}/issues/split`,
  `issue_split`) opens each in the held issue's repo with the goal's label and
  a `Split from` line naming the original, refuses a part with no acceptance
  check or no `Paths:` line (422, naming each part and what it lacks), and
  holds them by ref for a goal with no label. A part, known by its `Split from`
  line, is never held for its size: it was cut to size already. The goal takes them in
  as it takes any issue. When GitHub refuses a draft partway, the issues
  created before it are the split: they are recorded, with `issue_split` (and
  `issue_added` for each one a goal with no label holds by ref), and the 502
  names them. The original gets a comment naming its parts, and the same holds
  for an issue an approved plan split. It needs nothing from the operator: an
  update never asks about it. Once every part is closed, the sweep closes it
  with a comment saying so, and a goal counts it done from then on, even in a
  tracker with no done state to move it to. The split is recorded on its link (`split_into`), so later sweeps
  keep that reason and neither size nor split it again, and a second Split
  is refused (409). While a split is creating its issues, its claim
  (`split_into` empty) refuses Run anyway (409), so the original never
  starts beside its smaller issues; once the split ends, Run anyway is open
  again. Neither action takes an issue the goal removed (404) or one closed
  on GitHub since the last sweep (409): each reads the issue first, and
  records one it finds closed. A sweep that read the issue before a split began, and sized it
  small, looks at its link again under the row's lock just before it makes
  the task: a split issue, or one being split, stays held with the reason
  its split recorded, and one an operator removed from the goal, or another
  goal holds, makes no task. Nor does a sized issue whose link is gone,
  deleted when an operator removed its repo from the goal. Its sizing is recorded only on a link the goal
  holds and has not removed: when two goals' sweeps first see one issue at
  once, the one that did not record it first stops, and neither holds nor
  runs it. An operator can still act while the task is being made, after
  that last look. So the task is recorded on the issue's link only if the goal
  still covers it (`store.bind_task`): its repo is still the goal's, and the
  link was neither removed nor taken by another goal. That check reads the
  goal's row and the link's under the locks a goal update and Remove from
  goal take. When it fails, the sweep cancels the task it just made and
  records nothing, so a removed issue does not come back on the goal's
  board. When the task is recorded first, the operator's change meets an
  issue with a task: a goal update keeps it, and Remove from goal cancels it.
- An issue missing something runs, with the gap on its card. One with no
  acceptance checks, its own or the repo registry's defaults, runs with none,
  and its run asks the agent to run the repo's tests. A goal that sets
  `strict_issues` holds it instead, as it holds a large one, until it is run
  anyway. An issue with no checks that is not sized because sizing is off is
  refused, as before sizing. Once a later sweep sizes it, the
  refusal is cleared: a sized issue is held or run, never refused, so its
  card says one or the other.
- A held issue edited since it was sized is sized again on the next sweep,
  so adding what it lacked, or cutting it down, lets it go. A split one is
  not.
- The size is stored on the task as its `complexity` (small `low`, medium
  `medium`, large `high`, which also sets the router's `complex` floor), and
  the task's `max_usd` is the issue's own `Spend limit: $N` line, else the
  task policy's `max_usd_by_size` for its size.

A sizing call that fails, or a gateway the planner has no address or key for,
leaves an issue never sized waiting, with no task: the failure is the sweep's
error, and the next sweep sizes it again before it makes the task, so a large
issue, or one a strict goal would hold, never runs unsized. The sweep goes on
to the goal's other issues. A held issue whose new sizing fails keeps the
size it had.

#### Modalities

| | `fix` | `feature` | `new-system` |
|---|---|---|---|
| Work comes from | A standing queue: issues with the label, taken as they arrive | One spec in the goal statement, which a planning task breaks into dependency-ordered issues across the goal's repos | The goal statement. The planning task writes one phase of issues at a time, and replans after each phase from the repos as they are |
| First work | The issue itself | The plan | A design document and a skeleton that runs end to end, which every later issue must keep working |
| Parallelism | High: independent issues, one per repo area | The dependency graph | Low until the skeleton exists, higher after |
| Routing | `route_ceiling: standard`; escalation on failure | Floor `standard` | Floor `complex` for the design and skeleton issues, `standard` after |
| Bar to merge | A regression test that fails before and passes after, a small diff, no public API change | Each issue's acceptance checks, and a goal-level acceptance check once the last issue merges. Docs and a version bump per the repo's rules | Conformance to the goal's design document, the end-to-end check green, and each phase ending runnable |
| Operator checkpoints | None | Approve the plan before the first task runs | Approve each phase's plan |
| Measured by | Queue size, time to merge, cost per merged fix | Criteria met, spend against budget | Phase criteria met, then goal criteria met |

The planner reads these rules from the modalities document:

```json
{
  "version": "2026-11-07.1",
  "classifier": {"min_confidence": 0.7},
  "modalities": {
    "fix":        {"description": "Fix defects that already have an issue each. Small, bounded changes to code that exists.",
                   "intake": "queue", "plan": false, "approve": [], "latitude": 0, "auto_merge": true,
                   "route_ceiling": "standard", "parallel": 8,
                   "merge_bar": ["regression_test", "small_diff", "no_api_change"]},
    "feature":    {"description": "Add one capability to a system that exists, from a spec. Several issues across the repos it touches.",
                   "intake": "plan", "plan": true, "approve": ["plan"], "latitude": 0.25, "auto_merge": true,
                   "route_floor": "standard", "parallel": "graph",
                   "merge_bar": ["acceptance_checks", "docs", "version_bump"],
                   "goal_check": "acceptance"},
    "new-system": {"description": "Build something that does not exist yet, from a goal statement, in phases that each end runnable.",
                   "intake": "phases", "plan": true, "approve": ["phase"], "latitude": 0.5, "auto_merge": true,
                   "route_floor": {"design": "complex", "skeleton": "complex", "default": "standard"},
                   "parallel": {"skeleton": 1, "default": "graph"},
                   "merge_bar": ["design_conformance", "e2e_green", "phase_runnable"]}
  }
}
```

The planner fills a task's fields from its issue:

| Task field | From the issue |
|---|---|
| `external_ref`, `repo` | `github:<owner>/<repo>#<number>` and its repo |
| `title`, `description` | The issue's title and body, treated as untrusted |
| `acceptance_checks` | The body's `## Acceptance checks` list, else the registry's checks for the repo |
| `task_type`, `complexity`, `max_tier` | The labels `type:<task type>`, `complexity:<low, medium or high>` and `max-tier:<tier>`, within the modality's floor and ceiling |
| `depends_on` | `Depends on: <issue>` lines in the body, plus the tracker's blocked-by links |
| `paths` | `Paths: <path>, <path>` lines in the body: the areas the task locks, and the paths its run, review and fix prompts tell it to keep to |
| `goal_id` | The goal |

The repo registry lists the repos the meta-harness may
change, with each one's default branch, default acceptance checks and merge
policy. For this project they are `davecthomas/ai-api-unified`,
`davecthomas/ai-api-unified-http` and `davecthomas/meta-harness`.

A task waits in `blocked` until every issue it depends on is closed. Tasks
with no open dependency run in parallel, within the modality's parallelism and
the repo-area locks. The planner refuses a dependency cycle and shows its
issues. A task that waited on another repo's change uses that repo's merged
commit.

The sweep, the dependency order and the `blocked` state are built. The
`type:` label is read; the `complexity:` and `max-tier:` labels, the
cycle check, operator issues and the `meta-harness:failed` label are
planned.

Operators publish releases and deploy. An issue labeled `meta-harness:operator`
belongs to its goal, but the meta-harness never runs it: the operator console lists it
as needing an operator, and closing it unblocks the issues that depend on it.
A goal can therefore hold "publish ai-api-unified 3.31.0" between a library
change and the ai-api-unified-http change that pins the new version:

```mermaid
flowchart LR
    A["ai-api-unified#120<br/>library change"] --> R["ai-api-unified#121<br/>publish 3.31.0<br/>meta-harness:operator"]
    R --> H["ai-api-unified-http#50<br/>pin 3.31.0"]
    H --> M["meta-harness#30<br/>call the new endpoint"]
    D["meta-harness#31<br/>docs for the new option"]
```

Here ai-api-unified#120 and meta-harness#31 run at once. ai-api-unified-http#50
waits for an operator to close the release issue, and meta-harness#30 waits for
ai-api-unified-http#50 to merge.

Each pull request says `Closes <issue>`, so its merge closes the issue. A task
that ends without a merge leaves its issue open, labeled `meta-harness:failed`
with a comment holding the outcome, and the issues that depend on it stay
blocked. An issue labeled while the goal is active joins it, including the
issues a planning task writes.

An issue closed some other way than by its task's pull request (another pull
request, or by hand) has no work left. Each sweep cancels such a task when
it has opened no pull request of its own: queued, blocked, or running with
none. So a goal never pays to redo finished work, and the task frees its
repo area for the ones behind it. A task with a pull request is left to its
workflow, since its own merge is what closes its issue.

A goal is `met` when its success criteria pass, and `stalled` when every open
issue is blocked by a failed issue or waits on an operator issue, which raises
an alert. Its `max_usd` covers every task it queued. What happens once it is
spent is the goal's `budget_mode`. A `block` goal runs on, over budget, to its
blocking line, `max_usd` plus `budget_overage_percent` of it; from there the
meta-harness scheduler admits no more of its tasks and the goal becomes
`budget_exhausted`. Its running tasks share what it has left before that
line, run by run, and hold for an operator when that is too little (see the
review loop's budget hold), so a goal's tasks started together no longer
spend several times its budget. A `warn` goal keeps going: nothing is held
or refused for its budget. Each task's own spend limit is unchanged either
way.

Above every goal and task is the deployment's budget policy, the task
policy's `budget_policy`, which the Task policy page sets with a switch.
`block`, the default, leaves each goal's budget and each task's spend limit
to stop work as it says. Under `track` nothing is held, refused or ended for
either, and spend is still recorded and shown against them: every goal
behaves as `warn` whatever its own mode (the planner serves it no blocking
line, a `budget_exhausted` goal becomes `active` at its next sweep, and
there is nothing to resume), and a task that starts gets the ceiling for
one task as its spend limit, whatever its own `max_usd` says. No task runs
without a spend limit under either policy: the task policy's `max_task_usd`
($100.00 when it names none, at most $1,000) is the most one task may spend
across all its runs. Under `block` a task's limit is its own, or $10.00, and
never more than the ceiling. The Task API refuses a `max_usd` above the
ceiling when a task is made, retried or has its limit raised (422, naming
the ceiling). A task that reaches the ceiling holds for budget, as at any
spend limit, and an operator ends it or raises the ceiling in the task
policy first. A task
already running keeps the limit it started with. Services read a saved
policy within five seconds, and one that cannot be read counts as `block`.

Each sweep of a goal with a budget adds up what its tasks' runs have spent
and saves it on the goal (`planner/budget_api.py`). The first time the spend
reaches the budget the planner records `goal_budget_reached`, with the
spend, the budget, the mode and the blocking line; once per budget, so
raising the budget lets it be recorded again. A `budget_exhausted` goal is
swept like an active one, so its pull requests are still watched. The goal
the planner serves carries `budget_block_at` (the blocking line in dollars,
null in `warn` mode or with no budget), which admission and the budget hold
read so the line is worked out in one place, `budget_state` (`ok`, `over`
for past the budget and still running, or `blocked`) and, for a goal that
is over or blocked, `budget_resume_to`, the budget a resume would set.

A goal stopped by its budget resumes in one step:
`POST /v1/goals/{id}/resume-budget` raises its `max_usd`, makes a
`budget_exhausted` goal `active`, and answers each of its running tasks held
for the goal's budget with `raise`, so they look again at once; its queued
tasks start at the scheduler's next pass. With `add_usd` the budget rises by
that much, refused when the goal would still be stopped. Without it the new
budget is worked out (`planner/budget.py`): what the goal has spent, plus
the average cost of its finished tasks times its issues not yet done (the
default task spend limit for each when none has finished), rounded up to a
whole dollar. The answer says the old budget, the new one and how it was
worked out, and the change is recorded as `goal_changed` with
`resumed: true`. A `warn` goal has nothing to resume. A budget changed by
hand (`PATCH /v1/goals/{id}` with `max_usd`, `budget_mode` or
`budget_overage_percent`) takes effect at once too: the planner looks at the
goal's spend as a sweep would before it answers, so the goal's status,
`budget_state` and `budget_resume_to` are the new budget's, and its tasks
held for the budget are told to look again.

#### Latitude

A goal's `latitude`, from 0 to 1, sets how much scope the planner may add on
its own. It governs the planning task's prompt, the drafts the planner
accepts, and the loop's decision step.

| Latitude | The planner may | Example |
|---|---|---|
| 0 | Only the issues the goal names or people labeled. It drafts none, and it refuses a draft outside them. A `fix` goal at 0 is a pure queue | "Fix these twelve issues" |
| 0.25 | Draft the issues the statement needs and nothing beyond them. A draft must trace to a sentence in the statement | "Add the `route_ceiling` option" |
| 0.5 | Draft what the statement needs, plus the work it finds necessary on the way: tests the change needs, a doc the change makes wrong, a refactor the change needs to land | "Build the review loop" |
| 0.75 | Also draft improvements it finds in the goal's repos that serve the statement, as separate issues the operator sees in Inputs before they run | "Make the sample's end-to-end check faster and more thorough" |
| 1 | Keep going on its own: when the criteria are met it proposes the next goal from what it learned, and continues under the same budget until the budget or the operator stops it | "Improve the meta-harness" |

Above 0.5 every draft has a `latitude_reason`, one line on how it serves
the statement, shown beside the issue in the operator console. Latitude changes only
what the planner may propose; the merge bar, the budget, the repo list and the
control plane's sole write access to the code host stay fixed. The defaults are
`fix` 0, `feature` 0.25 and `new-system` 0.5, and an operator can change
latitude on the Goal view while the goal runs.

#### Planner

The planner owns the goal loop, the `goal`, `plan`, `issue_link` and
`pull_request_end` tables and
the issue dependency graph. It is the only component that talks to an issue
tracker, through one adapter interface:

| Adapter operation | Purpose |
|---|---|
| `list_open(repo, label)` | The open issues with the goal's label for one repo |
| `list_assigned(repo)` | The open issues a person assigned, in the tracker, to the account the meta-harness signs in as, for a goal that asked to take them. Empty for a tracker with no such way |
| `read(ref)` | One issue: title, body, labels, state, and the issues that block it |
| `ensure_label(repo, label)` | Make sure an issue created for the repo can carry the label |
| `create_issue(repo, title, body, labels)` | Create an issue, for an approved plan's draft or a split. Returns it with its `ref` |
| `comment(ref, text)` | Add a comment to the issue. Returns the comment's address |
| `mark(ref)` | Make the issue carry the meta-harness's label. The adapter calls it itself before every write |
| `set_status(ref, status)` | Move the issue to `todo`, `in_progress`, `in_review` or `done`, as far as the tracker has such a state. Returns whether it changed |
| `link_pull_request(ref, pr_url, title)` | Record the pull request on the issue, where the tracker does not find it by itself |
| `url(ref)` | Where a person reads the issue |
| `webhook(payload)` | Planned: turn a tracker's webhook into `issue_opened`, `issue_edited`, `issue_labeled`, `issue_closed` events, so the Inputs view and the goal loop never poll |

The interface is `meta_harness.trackers.base.Tracker`, and each tracker is
one adapter module beside it (`src/meta_harness/trackers/`). `repo` is always
the code repo: an adapter whose issues live elsewhere maps the repo to that
place itself. A failed call raises `TrackerError`, with whether to try again,
so no caller knows which tracker it spoke to. Outside an adapter a ref is
opaque: stored, compared and passed back. `repo_of(ref)` is the one more
operation: the code repo an issue's work is done in.

**The registry of supported trackers** (`trackers/registry.py`) is the list
of adapter modules. Each describes itself in a `SPEC`: its name and title, its
settings (for the console to draw its form), the secrets its sign-in needs,
a check of its settings, how to build it, and whether the planner tells it
how an issue's task is going (`writes_progress`: Jira yes; GitHub no, since a
pull request's `Closes` line links and closes its issue). A new tracker is
one module and one line in the registry.

**The meta-harness's own tracker** (`trackers/own.py`, scheme `mh`) keeps
issues in the meta-harness and in no outside system: rows of
`planner.own_issue`, with refs `mh:<id>`. It is an adapter like the others, so
the sweep, sizing, dependencies, the board and the met check read its issues as
they read any. It is always there, has nothing to set up, and is not part of
the `trackers` configuration. It is what a goal that uses no tracker uses: its
work is planned from its description, every task of its plan is an issue
there, and its pull requests are the only record outside the meta-harness. The
planner tells it how each task goes, and a task's merge closes its issue, since
nothing else would. A goal on another tracker may also hold such issues: its
tracker is then `WithOwn` (`trackers/router.py`), which sends each call that
names an issue to the tracker its ref names. The `trackers` configuration may
set up no tracker at all, and every goal then uses none.

**Which trackers are set up, and the default**, is the `trackers`
configuration, versioned and checked like the other kinds
(`config/trackers.json` ships GitHub alone; `config/trackers.local.json`,
which git ignores, replaces it). It holds what is true of a whole tracker,
never of one goal:

```json
{"default": "jira",
 "trackers": {"github": {},
              "jira": {"site": "https://example.atlassian.net",
                       "default_project": "PROJ",
                       "projects": {"PROJ": {"issue_type": "Task",
                                             "statuses": {"in_review": "In Review"}}}}}}
```

One Jira site holds many projects, and a workflow belongs to a project. So
Jira's setup lists the projects the meta-harness works in (`projects`), and
what depends on a workflow is set for each: the type of an issue the planner
creates (`issue_type`) and the project's own name for a status (`statuses`).
`default_project` is required and is one of those listed: the project a new
goal starts with. A save without one is refused. A configuration saved
before projects were listed holds `issue_type` and `statuses` for the whole
site and no `projects`; it keeps working, any project allowed, until its next
save, when the console moves both under the first project added.

A sign-in is never in it: Jira's is the secrets `MH_JIRA_EMAIL` and
`MH_JIRA_TOKEN`. `GET /v1/trackers` lists every supported tracker with its
settings' description, whether it is set up and signed in, the default, and
`goal_defaults`, what a new goal on it starts with (Jira's default project).

**A goal says where its issues are**: its repos, as every goal does; its
tracker (`goal.tracker`), the default unless it names another that is set up;
and what that tracker asks of a goal (`goal.tracker_settings`), which the
adapter declares in its `SPEC` as `goal_settings`. Jira asks for the project:
one of those its setup lists (422 for another, naming the listed ones), and
the default project when the goal names none. GitHub asks for nothing. All
three are fixed when the goal is created, and every ref it holds is its
tracker's, and for Jira its project's (422 otherwise). A goal from before
goals named a tracker uses GitHub. A project an open goal uses cannot be
removed from the list: the save is refused, naming the goal, as for a repo.

**Names are picked, not typed.** A setting's options come from
`GET /v1/trackers/{scheme}/choices/{setting}`, so no form or route is written
for one tracker. The query says what the setting belongs to. For Jira:
`projects` is every project of the site the account sees, for the setup's
list; `project` is the listed projects, for a goal; `issue_type` and
`statuses` take `project` and list that project's own issue types (sub-task
types left out, since they need a parent) and the status names of its
workflows (`GET /project/{key}/statuses`), and no other project's, since a
status of another workflow moves none of its issues. An adapter describes a
list of entries with settings of their own as a setting of kind `entries`
(Jira's projects) and a pick among them as `entry_of` (the default project),
and the Issue trackers page draws both from that description. Each field
offers its list as the operator types, refuses a name the list lacks, takes
the list's spelling of one typed in other capitals, and marks a saved name the
list no longer has. When the list cannot be read it says why and takes a typed
name. On the New goal form a `choice` setting is a pick from the list and never
free text, so nothing half-typed reaches the calls that read the goal's
issues; Jira's listed projects stay offered, by key, while Jira cannot be
reached. This is the console's rule for any value a service can list
(`AGENTS.md`, Console).

`Trackers` (`trackers/router.py`) is every supported tracker as one: a call
that names a ref goes to the tracker the ref's first part names; a call made
for a goal goes to the goal's tracker bound to its `Source`, its repos and
tracker settings (`Tracker.bound`); any other (the label and issue lists
before there is a goal, which take `?tracker=` and `?settings=`) to the
default. The repo registry's `tracker` field decides nothing, and the console
no longer shows it.

**Which repo an issue's work is done in**, where the tracker's issues are not
a repo's own (Jira): the goal's repo when it has one. A goal with several
repos reads it from the issue, a `Repo: owner/name` line in its description
or a `repo:<name>` label, by the repo's full name or its name alone. An issue
that names none of the goal's repos is held, saying which repos and how to
name one, and becomes a task once it does. An issue the planner creates for
such a goal carries the `Repo:` line.

In the console, Configuration has an Issue trackers page
(`console/src/features/trackers/TrackersView.tsx`), drawn from `GET
/v1/trackers`: every supported tracker with whether it is set up, whether its
sign-in is there (a missing one is named by its secrets, with where to add
them), Set up, Make default and Remove, and its settings edited in place:
Jira's site, its issue type and its status names. Edits stage and save as
one checked version, like the other kinds. The New goal form offers the
trackers that are set up when there is more than one, starting on the
default, then asks what the chosen tracker asks of a goal
(`features/trackers/GoalTrackerFields.tsx`): for Jira its project, chosen
from the site's projects by name or typed by key. Its label suggestions, the
label's issues and the issue picker read from that tracker and project, once
the goal has said what the tracker asks. A goal's page shows its tracker and
what it told it. A ref reads without its tracker and links to its issue for
any tracker (`features/trackers/refs.ts`): GitHub's address comes from the
ref, another tracker's from the `issue_url_prefix` the tracker reports.

As built, the GitHub adapter (`trackers/github.py`) has every operation but
`webhook`: `done` closes an issue and `todo` opens it again, the statuses
between change nothing, since a GitHub issue is open or closed, and
`link_pull_request` does nothing. The Jira adapter (`trackers/jira.py`)
works with Jira Cloud's REST API version 2, whose descriptions are text:
- An issue's ref is `jira:<KEY>`.
- `list_open` is the goal's project's issues not in a Done status, with the
  label, searched once for all the goal's repos. An issue in a Done-category
  status is closed.
- `list_assigned` is the same project's open issues whose assignee is the
  Jira account the meta-harness signs in as (`assignee = currentUser()`),
  whatever their labels, for a goal whose tracker setting `assigned` is
  `yes`; no call is made for any other goal.
- `choices("project")` lists the site's projects the Jira account sees.
- A status name or issue type the site lacks: the console refuses it at
  entry, from `choices`; saving the configuration (`PUT /v1/config/trackers`)
  makes no call to Jira and keeps it; the adapter reports it at first use. The
  first `set_status` to a configured status name, and the first `create_issue`
  of the project's issue type, check the name against the same `choices` and
  log a warning that names the setting, the value and the names the site has.
  A check that cannot reach Jira is silent and repeats at the next use.
- What blocks an issue is its "is blocked by" links and the keys its
  `Depends on:` lines name.
- `set_status` moves the issue through its workflow: to the status the
  settings name for ours; else to one with the usual name for it (In
  Progress or In Dev, Done or Closed, any name with "review"); else to one of
  the matching Jira category. The name comes before the category because a
  workflow's categories do not always say what its statuses mean: one may
  class "Ready for Development" as in progress and "In Code Review" as not
  started. A workflow with no such move is an error that lists where the
  issue can go.
- `link_pull_request` adds a remote link whose id is the pull request's
  address, so adding it twice changes the one link.
- A label with a space is refused before any issue is made.
An issue is one task, by its ref, so what the task was made from can fall
behind the issue. A task that ended canceled or failed is made over when a
control-plane service submits its issue again (`tasks.store.renew`): its
title, description, acceptance checks, paths, goal and repo become the
submitted ones, its size and spend limit too when submitted, and it goes
back to the queue. One with a pull request keeps it and resumes at its
review: the workflow goes on with one still open and starts a new first run
when it was closed unmerged. A task still running, waiting or done is left
as it is. The sweep does this for an open issue whose task was canceled
(`_retake`), so the task goes back to work from what the issue says today.
An issue held by a goal that has ended (met or canceled) is
released for a new goal with its label to take
(`planner.store.release_from_ended_goal`); an open goal's issue stays its.

**Every issue the meta-harness creates or changes carries its label**,
`ai:meta-harness` unless the `trackers` configuration names another
(`label`, set on the Issue trackers page; up to 50 characters, no space or
comma, since a Jira label has none). The rule is the adapter's, not its
callers': an adapter creates every issue with the label beside the ones it
was given, and labels an issue (`mark`) before each write to it: a comment,
a status that moves it, a pull request's link. So a plan's issues, a split's,
a progress comment and a reply all leave the issue labeled, and a new call
site cannot forget to. Both trackers make a label when an issue first
carries it, and adding one an issue has changes nothing; an adapter
remembers which issues it labeled since the service started, so the label
costs one call an issue. An issue the meta-harness only reads is not
labeled.

**Handing an issue over in the tracker.** A person gives an issue to the
meta-harness where they work. On every tracker the goal's label does it.
On Jira, assigning the issue to the meta-harness's account does it too, for
a goal created with "Take issues assigned to the meta-harness" on (its
tracker setting `assigned`, a `switch`, shown on the New goal form and on
the goal's page, and fixed when the goal is created like its project). Each
sweep lists the label's issues and the assigned ones (`list_assigned`) and
takes both in the same way, so such a goal needs neither a label nor picked
issues (`Supported.takes_assigned` tells goal creation so). GitHub has no
assignment: the meta-harness acts there as a GitHub App, and GitHub assigns
issues only to people. A comment that mentions the meta-harness is not
read on either tracker: without webhooks, finding one would mean reading
every issue's comments each sweep.

**Telling a tracker why an issue is not started**
(`planner/tracker_replies.py`). An issue handed over and then held or
refused would otherwise show nothing in its tracker. Each sweep comments on
each of the goal's open issues that has no task and a reason
(`held_reason`, else `refused_reason`): the reason, which says what to do,
that the issue is read again each sweep, and that an operator can act from
the goal's page. It comments once for each reason: the reason last given
is kept in `issue_link.replied`, and a comment that failed is tried again
by the next sweep. Every tracker is told, GitHub too. A sweep's result
lists the refs it commented on (`replied`).

**Telling a tracker how a task goes** (`planner/tracker_progress.py`) is
only for an adapter that asks for it (`writes_progress`). Each sweep, and
each follow-up of a goal no sweep covers, works out what each issue with a
task should now say and writes only when that differs from what was last
written (`issue_link.progress`):

| The task | The issue's status | Comment |
|---|---|---|
| running | in progress | that work started, with the task's id, said once; or, after a review a person was waiting on, that work is going on again on its pull request. The agents' own review of a new pull request, and a merge an operator asked for, say nothing and leave the issue where it is |
| ready or review unresolved, with a pull request | in review | the pull request, and whether it is ready to merge or its review left findings |
| closed | done | that the pull request merged |
| failed or canceled | unchanged | why |

With a pull request, its link is recorded on the issue. The status goes
first and the comment last: the status and the link are safe to repeat, so a
write that failed is made again by the next sweep without doubling a comment.
For Jira, done is what closes the issue, and so what lets its goal be met. A
sweep's result lists the refs it told (`told`). A sweep
every `MH_PLANNER_SWEEP_SECONDS` stands in for the webhook. The Linear
adapter is planned. The sweep also reads the pull requests of `ready` and
`review_unresolved` tasks and their checks (Review loop), and of `canceled`
tasks that left one open, until it merges or closes, through the run
service's code-host functions, with a token that has `pull_requests: read`,
`checks: read` and `statuses: read`. A goal no sweep covers (met, canceled,
paused or stalled) is followed up every hour instead: the labels and title of
each issue it holds that has a task are read again, and its canceled tasks'
pull requests watched for their end, so costs by label and by outcome stay
true after the goal ends. A follow-up also records each issue the goal holds
as open that has closed (`issue_closed`), and the planner does the same when
the goal's page is read, at most once a minute, so a goal no sweep covers
never shows the state its last sweep saw as today's. A follow-up takes
nothing in and changes no task's or goal's status.
The canceled tasks of no goal, which no goal's sweep lists, have their open
pull requests watched for their end every hour too.
`pull_request_end` notes each task whose pull request's end a sweep recorded,
so it is read no more.

The adapter syncs both ways. The planner pulls in work people write: an issue
labeled in GitHub, Jira or Linear joins its goal, and an edit to its body,
labels or dependencies updates the task before it runs. It writes back what it
generated and what happened: the issues a plan drafted, each task's state as a
label or status (`meta-harness:running`, `meta-harness:pr-open`,
`meta-harness:failed`), the pull request link, the outcome, and the close on
merge. One `issue_link` row per issue holds the tracker's state beside the
meta-harness's, and the planner reconciles them on every webhook and on a
periodic sweep. The tracker owns what the work is, and the meta-harness owns
what happened to it. An issue closed by hand while its task runs cancels the
task, with the reason recorded.

A `ref` names its tracker: `github:<owner>/<repo>#<number>`, `jira:<key>` or
`linear:<identifier>`. Each repo in the repo registry names its tracker, and
for Jira or Linear the project or team and the field or label that names an
issue's repo. The GitHub adapter works through the GitHub App, and the Jira and
Linear adapters plug into the same interface.

A `feature` or `new-system` goal's issues come from a planning task,
`task_type: plan`, which the planner creates with the goal statement, the
modality and, for a later phase, what earlier phases built. The planning run
reads the goal's repos and drafts issues with its `create_issue` tool, each
in the repo it changes, with acceptance checks and what it depends on.

**The planning configuration** is a configuration kind of its own, `planning`,
versioned and edited in the console (Configuration, Planning) like the others,
and seeded from `config/planner.json`'s `planning`: the candidate a planning
task runs on (`candidate`, whose profile must exist and have the `create_issue`
tool), what one planning task may spend (`max_usd`), which goals are planned
(`goals`), whether a plan's new tasks go to the goal's tracker unless the
operator says otherwise (`track_new_tasks`, true when left out), and how much
of a goal's issues the planner is shown (`max_issues`, `issue_chars`).

**The planner's updates.** The planner keeps the operator up to date at the
head of each goal's page (`planner/updates.py`): where the goal stands, what
changed, what needs the operator and what happens next, in plain language.
Each pass of the planner's loop gathers the facts its own records hold about
every open goal (its plan, each issue and its task, what is held and why) and
reduces them to a digest; an update is written, with one small model call
through the HTTP Gateway, only when the digest differs from the last update's,
so a goal that stands still costs nothing. A goal that closes gets one last
update. The model is given the facts and its last update, and told to say only
what the facts say. Updates are rows of `planner.goal_update`, read with
`GET /v1/goals/{id}/updates`; `POST` to the same path writes one now. Each is a
`goal_update_written` event, whose cost is counted as planning. The planning
configuration's `updates` turns them off, names the model (goal drafting's when
left out), and bounds them: `max_per_goal` updates for a goal, at least
`min_seconds` apart. An update that asks something of the operator does not
wait: it is replaced as soon as the goal moves, and an approved plan's goal
gets its update as soon as its first sweep has made its tasks. One that cannot be written is logged, said on the page,
and tried again at the next pass.

**The planner's voice** (`planner/voice.py`). What the planner writes for the
operator, its updates and a plan's summary and gaps, follows the planning
configuration's `voice`: `tone` (`casual` as shipped, `balanced`, `formal`) and
`detail` (`low` as shipped, `medium`, `high`). At low detail it names no file,
route, function or command, keeps to a sentence or two, and raises only what the
operator needs to know or decide. The settings become instructions in the
update's model call and in the planning task's prompt for `plan_summary`. A
task's own description, which an agent works from, is not shortened by them;
the plan's review folds it under the task's title.

The planner speaks to the operator in one place: its panel at the head of the
goal's page. The plan's section shows the plan itself (its order of work, its
decisions on the goal's issues, its new tasks and the approval); the plan's
summary and gaps reach the operator through the planner's update, which, while
a plan waits for approval, says what it does and lists approving it and each
gap worth weighing under "Needs you". A goal with a plan keeps its settings
folded to one line, so the plan comes first.

Every "Needs you" item comes with what resolves it. The update's model picks an
action for each (`approve_plan`, `merge`, `review`, `open_task`,
`raise_budget`, `open_issue`), and `updates.actionable` checks it against the
goal's records before it is saved: a merge needs a task whose pull request
passed review, an approval a goal that is planning, and an action the records
do not support becomes the nearest one they do. A task's or an issue's own state
outranks the model's pick: a review no fix settled is `settle_review`, a task
ready to merge `merge`, a failed task `retry`, and an issue held for its size
`split_or_run`. The panel goes further and reads each need's task as it is
now, so an update written before the operator acted still offers what fits:
Merge as is, Try another fix and See the findings on a review no fix settled;
Merge on one ready; Retry on one failed or canceled; Split and Run anyway on a
held issue; and "Under way again" once the task runs. Acting from the panel
asks the planner for a new update at once. The panel shows each as a
button that resolves the need on the spot (approve the plan, merge after a
confirm) or opens where it is resolved (the task, its changes, the goal's
budget, the issue).

**Suggested fixes** (`planner/suggestions.py`). When a planning task's plan is
saved with gaps, one small model call sorts them: a gap a change to the plan
itself would settle (two tasks that would clash run in order, a missing step
becomes a task) is a fix the planner makes; one only the operator can settle
stays a question. For the fixes, a second planning task revises the plan with
them as its note. Its result is kept in `planner.plan_suggestion`, apart from
the goal's plan versions, so the current plan can still be edited or approved
while it drafts. The planner's update then asks the operator to accept it
(`accept_fix`), with a line for each change. Accepting
(`POST /v1/goals/{id}/suggestion/accept`) makes it the next plan version,
source `suggested`, which is still approved as any plan is; dismissing it, or
editing the plan or asking for a new one, leaves it behind. At most one fix is
drafted per plan version and none for a version that was itself a fix. The
planning configuration's `suggest_fixes` turns them off. The sorting call is a
`plan_fixes_assessed` event, counted as planning cost; accepting and dismissing
are `plan_fix_accepted` and `plan_fix_dismissed`.

**Task names** (`planner/naming.py`). A task's id says nothing to a person and
its title is a sentence, so each task has a short name: two to four words, at
most 28 characters ("Hint text contrast"), kept in `tasks.task.name`. A task is
created without one. Each planner pass reads the tasks with no name
(`GET /v1/tasks/unnamed`: those of the last 30 days, never a review, fix or
smoke task) and names them a goal at a time, one small model call for up to 25
of a goal's tasks, told the names the goal already has. It saves each with
`POST /v1/tasks/{id}/name`, which numbers a name another task of the goal has
("Hint text contrast 2"), so a name says which task within its goal. The
console calls a task by its name wherever it showed the id, with the title
beside it or on hover and the id behind a copy button; a task not yet named is
called by its title. A call that fails is logged and its tasks are tried again
after ten minutes. Each call is a `task_names_written` event, counted as
planning cost.

**Every goal is planned.** With the planning configuration's `goals` set to
`all` (as shipped; `from_description` plans only a goal that plans its issues
from its description, as before), a goal that takes existing issues is planned
too: it is saved `planning` (`goal.planned`), and none of its tasks is made
until an operator approves its plan. Its planning task is given the goal and
every issue it would take, read together: its label's, those assigned to the
meta-harness, and those picked for it, less any another open goal holds
(`planning.max_issues` of them, `planning.issue_chars` of each one's text). It
records its plan with three tools. `create_issue` writes a new task, as above.
`plan_issue` records its decision on one existing issue: `keep` it, `split` it
into new tasks that replace it (`replaced_by`), or `leave_out` one that is a
duplicate, done already or not this goal's, each with its reason and, for a
kept one, what it waits on. `plan_summary` records how the plan reaches the
goal, and its gaps: what the goal asks for that no task covers, a risk, an
assumption. A profile that names `create_issue` gets the other two with it,
since a deploy never rewrites a deployment's stored profiles. The harness
server reports them as `issue_planned` and `plan_summarized`.

A plan (`planner/plans.py`) so holds its new tasks (`drafts`), its decision on
each existing issue (`items`; one the planner said nothing of is kept), its
`summary` and `gaps`, and the issues it was made over (`issues`). It is invalid
when it runs nothing, a decision is on an issue that is not the goal's, a split
names no new task or one the plan lacks, a task waits on an issue the plan
splits or leaves out, or the dependencies across issues and new tasks make a
cycle. An operator edits decisions and new tasks alike; each save is a version.

Approving applies the plan (`planning.approve`). Its new tasks become issues,
in the goal's tracker or, when the operator says so (`track: false`), in the
meta-harness's own (`mh:` refs); a goal with no tracker always keeps them
there. Each kept issue is taken into the goal with what the plan says it waits
on (`issue_link.planned_at`, `plan_depends_on`), which the sweep adds to what
the issue's own text names. A split issue stays the goal's, held, with the
tasks that replace it. One left out is marked removed, so the goal's label
does not sweep it in. An issue the plan took in is not held again for its size
or its gaps: the plan was the review. Its size still sets its task's spend
limit. An issue that joins the goal after approval (its label put on later)
is held as a proposed addition, for an operator to add as it is or to plan the
goal again; one an operator picks by ref afterward has been looked at and runs.

A planned goal that is under way can be planned again (`POST
/v1/goals/{id}/replan`, Plan again on its page): it goes back to `planning`,
the tasks already running go on, and the scheduler starts none of its others
until the new plan is approved. The planner is given only what no task has
started on. An issue with a task under way, one a plan split, and one removed
from the goal are not the new plan's to decide.

Built for `feature` (#72, #73): a `feature` goal starts in `planning`; one
created without a label gets its own, `meta-harness:goal-<the first 8
characters of its id>`. The planner starts one task with `task_type: plan`,
pinned to the candidate `config/planner.json`'s `planning` names, with that
file's spend limit. Its prompt (`plan-v1`) is the goal's title and
statement, its repos, and the rules for drafts: one issue is one pull
request, each body states its `## Acceptance checks` and its `Paths:`, and
`depends_on` names drafts and existing issues. The scheduler holds every
other task of a goal that is not `active`, and admits the plan task of a
goal in `planning`. The run changes no file, so the RunTask workflow ends a
plan task `closed` after its run, with no publish, no pull request and no
review loop (behind the `plan-task` patch). When the task ends, the planner
reads its `issue_drafted` events: the last call for each `draft_id` is the
draft, and together they are the goal's plan, version 1 (`plan_saved`). A
plan is invalid, with each reason recorded, when it has no drafts, two
drafts share an id, a draft has no title or no acceptance checks, names a
repo outside the goal or a dependency that is no draft and no issue, or the
drafts' dependencies make a cycle; a planning task that failed leaves an
empty plan that says why. The goal waits in `planning` until an operator
approves: they may edit the drafts (each save a new version), or ask for a
new plan with a note, which starts another planning task. On approval the
planner makes sure the label exists, creates the issues in dependency
order, each body ending with a `Depends on:` line naming the issues its
dependencies became, records each issue against the plan as soon as it
exists, holds it for the goal, and makes the goal `active`, whose sweep
then takes the issues in as it takes any labeled issue. Creating issues
needs the GitHub App's `issues: write`. Not built: the modality's rules on
a plan, and plans for a `new-system` phase. When a `new-system` phase ends with its
criteria unmet, or a failed task leaves dependents blocked, the planner
creates a planning task with the failure as input, and that plan goes through
the same approval.

## Data model

| Owner | Entity | Fields |
|---|---|---|
| Task service | task | id, caller, external_ref, repo, task_type, parent_task_id, goal_id, depends_on, pr_url, head_sha, priority, spec, acceptance_checks, created_at, status (including `blocked`, `taken_over`, `ready` and `review_unresolved`), owner (the operator, while taken over), status_changed_at (when it last changed status), recoveries and spent_carried_usd (the supervisor's: how often it was sent back for want of a workflow, and what it had spent then), drives (how often the supervisor sent it back for its merge or review since an operator's last retry) |
| Meta-harness scheduler | task_hold | task_id, kind, reason, detail (what the reason names), since, last_checked. One row per queued task the scheduler holds |
| Supervisor | standing | task_id, owner (workflow, scheduler, planner, supervisor, operator), situation, reason, ask, since, checked_at, attempts, acted_at, last_error. One row per task that has not ended closed |
| Planner | goal | id, created_by, title, modality, modality_source (classifier, operator), modality_confidence, repos, label, statement, success_criteria, latitude, auto_merge, max_usd, max_parallel (null: the system's), strict_issues, budget_mode, budget_overage_percent, spent_usd (at the last sweep), priority, status (planning, active, paused, met, stalled, budget_exhausted, canceled), phase, created_at, started_at (null until the goal first becomes active), closed_at |
| Cost service | daily_cost, harvest | One row per day, source (`model`, `cloud`), item (the kind of run, or the billed service) and the dimensions the source has (goal, task, repo, model, engine, operator), with its dollars; and how far each source has been read |
| Planner | console_user | email, role (admin, user), status (invited, active, deprovisioned), invited_by, invited_at, last_sign_in_at. Who may use the console, and as what |
| Planner | user_settings | identity, settings (jsonb), updated_at |
| Planner | plan | goal_id, version, drafts (each `draft_id`, `repo`, `title`, `body`, `labels`, `depends_on`), invalid (why it cannot be approved; empty when it can), source (`planned` by a planning task, or `edited` by an operator), task_id, note, created (the issue each draft became), created_by, created_at. Planned: phase |
| Planner | issue_link | ref (tracker-qualified), goal_id, task_id, repo, state, depends_on, dropped (an operator removed the issue from the goal: the row stays so the label does not take it in again), size and sizing (what the issue's sizing found: reasons, missing, split, and the issue's paths and checks, with the model and its cost), held_reason (why it waits for an operator; null when it does not), run_anyway, split_into (the issues an operator split it into; null until split), title and labels (the issue's, refreshed by each sweep, for an issue with a task whether the label still lists it or it has closed, and hourly once its goal is met or ended; labels give costs by label), last_webhook_at |
| Planner | pull_request_end | task_id, outcome (`merged` or `closed`), recorded_at: the tasks whose pull request's end a sweep recorded, so a canceled task's pull request is read only until it ends |
| Meta-harness run service | run | id, task_id, attempt_no, kind (initial, fallback, followup, retry), candidate, harness_profile, profile_version, model, model_policy, image, sandbox_id, state, started_at, running_at, ended_at, end_reason, fallback_reason |
| Meta-harness run service | routing_decision | id, task_id, attempt_no, request_id, route_tier, route_method, route_confidence, model_pin, candidates (the tier's candidates, in order), thinking, config_id, config_version, classifier_usd, route_floor, route_ceiling, explored, created_at |
| Meta-harness run service | operator_review | task_id, round, operator, claimed_at, verdict, findings, submitted_at, released_at |
| Configuration | config_version | kind (tiers, profiles, repos, task_policy; modalities and access planned), version, document, edited_by, note, origin (seed, edit, rollback), rolled_back_from, created_at |
| Configuration | active | kind, version, changed_at |
| Pool manager | sandbox | id, image, executor, executor_ref, state (provisioning, warm, leased, kept, draining, unhealthy, destroyed), created_at, destroyed_at, destroy_reason |
| Pool manager | lease | run_id, sandbox_id, harness_session_id, session_state (new, kept, restored), granted_at, expires_at, released_at, release_reason |
| Meta-harness event service | event | event_id (UUID, unique), position (assigned on insert, monotonic), event_type, schema_version, time_utc, source (task, meta-harness scheduler, dispatcher, route, run, workflow, pool, analyzer, gateway), actor, run_id, task_id, session_id, request_id, seq, trace_id, config_id, config_version, tags (jsonb), payload (jsonb) |
| Meta-harness event service | event_type | name, schema_version, json_schema, retention_class |
| Metering service | model_call | run_id, seq, turn, purpose (turn, compaction, auxiliary), tier, method, engine, model, thinking, input_tokens, cached_input_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens, usd, latency_ms |
| Metering service | attempt_usage | run_id, usd_total, calls, input_tokens, cached_input_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens, cache_hit_rate, active_hours |
| Metering service | check_result | task_id, check_name, kind (ci, acceptance, merge), passed, observed_at |
| Metering service | task_outcome | task_id, goal_id, ci_share, merged, review_rounds, review_loop_usd, total_usd, wasted_usd, active_hours, dollar_hours, closed_at |
| Metering service | spend | caller, period, cap_usd, spent_usd |
| Harness analyzer | proposal | id, kind (tier, profile, task_policy), evidence, proposed_change, status (open, approved, rejected), decided_by, decided_at, config_version |

The `run` row records the candidate a run started on, and `model_call` records
which model answered each turn, which the harness analyzer reads. The event service
indexes the `event` table on `(run_id, position)`, `(run_id, time_utc)` and
`(event_type, time_utc)`. The metering tables are projections of it, and the metering service rebuilds them by replaying it.

Stores outside Postgres have one owner each: the meta-harness scheduler's repo-area locks,
the meta-harness run service's Temporal namespace and code-host rate-limit buckets, the
pool manager's artifact store, and the HTTP Gateway's run grants and per-run
spend counters.

## Observability

**Transient failures are retried before anything reports them.** Every HTTP
client the services build uses `meta_harness/retrying.py`, an httpx transport:
up to five tries with exponential backoff and jitter, inside a 45-second
deadline (below Firebase Hosting's 60), honoring `Retry-After` and GitHub's rate
limit reset. A request that never reached the service, or was turned away with
429 or 503, is always repeated. One that may have been acted on (a read timeout,
a dropped reply, 500, 502, 504) is repeated only when that is harmless: GET,
HEAD, OPTIONS, PUT, DELETE, and POSTs the client marks safe (Jira's issue search,
event batches, which carry their own ids, and read-only Google Cloud queries).
Each retry is logged as a warning, a recovery at info and a final failure as an
error, by method, host and path, never the query string, so a recurring failure
shows under Admin, Logs. The edge's forwarding loop follows the same rule while
a service restarts, and the console's `call` retries the same way, logging to
the browser's console. `tests/test_retrying.py` fails a client built without the
transport.

The VM reaches the internet through Cloud NAT, which limits the ports open to one
destination. The platform stack allocates them dynamically (1,024 to 16,384) and
frees a closed one after 30 seconds, and the Jira adapter keeps one pool of
reused connections, since all of Jira is one address.

Meta-harness domain events go to the event service, and logs, traces and
metrics go to OpenTelemetry. Every domain event has its trace id, so the
two join.

### Event service

The event service is the record of what happened in every task and run. It
owns the `event` table, the per-type schema registry, position assignment,
partitions, retention and the commit notification, and it calls no other
component.

| Operation | Contract |
|---|---|
| `append(events)` | Idempotent on `event_id`. The event service validates each meta-harness domain event against its event type's registered schema in the schema registry. The event service rejects an unknown event type or invalid payload back to the producing meta-harness service as a bug. The event service client library never retries a rejected append. |
| `read(run_id, from_position)` | A meta-harness run's meta-harness domain events in `position` order, paged |
| `tail(filter, from_position)` | A live stream of meta-harness domain events matching a filter (run, task, type, source), in `position` order, resumable from any position |

Meta-harness services append through the event service client library:

| Producer | Events |
|---|---|
| Task service | `task_created`, `task_status_changed`, `task_cancel_requested`, received code-host pull request webhooks |
| Planner | Built: `goal_created`, `goal_changed` for every change to a goal's title, settings or status (with `resumed: true` for a budget resume), `goal_budget_reached` the first time a goal's spend reaches its budget (the spend, the budget, the mode and the blocking line), `goal_drafted`, `goal_titled` with the model and its cost, `issue_added` and `issue_dropped` for issues picked by ref (and `issue_added` for each issue an approved plan created), `plan_requested` when a planning task starts (with the operator's note on a replan), `plan_saved` for each plan version (its source, how many drafts, and why it is invalid if it is) and `plan_approved` with the issues created, `issue_sized` when an issue is sized before it becomes a task (its size, reasons, what it is missing and the suggested split, with the model and its cost), `issue_released` when an operator runs a held issue anyway and `issue_split` with the issues a held one was split into, `issue_joined` (with the issue's size and the spend limit it set, for a sized issue), `issue_refused`, `issue_closed`, `task_unblocked` and `goal_met` when a goal is met, on its own or by an operator. For a `ready` or `review_unresolved` task's pull request, which no workflow watches: `pull_request_checks` for a new head commit's settled checks, `pull_request_merged` and `pull_request_closed`, on the task and tagged with the goal, with the workflow's event id for the same fact. Planned: `goal_classified`, `plan_drafted`, `plan_approved`, `issues_created`, `criteria_evaluated`, `planning_task_created`, and the tracker webhooks it received |
| Meta-harness scheduler | Admission decisions: `task_admitted`, and `task_held` with the reason and the kind of hold, recorded when a task's reason is new or has changed. `kill_switch_changed` with the operator as actor |
| Meta-harness run service: RunTask workflow, `route` and run activities | `task_routed` from the meta-harness run service `route` activity result. One `run_prompt` per run, recorded on the task with the run's `run_id` before the run starts by the `record_prompt` activity (behind the `run-prompt` patch), which appends it at once rather than through the client outbox, with a stable event id, so it is stored, ahead of the run's own events, before `run_harness` starts: the run's `kind` (`task`, `review` or `fix`), its `template` (`repo-task-v1`, `review-v4` (`review-v3` before it showed the description), `fix-v1`, or `inline` for the caller's own text) and the filled-in `prompt` sent to the harness server. One `attempt_finished` per run from the meta-harness run service run activity Collect result, joined to its pool manager sandbox lease by `run_id` |
| Meta-harness run service: run activity (Wait step) | The per-run sandbox's harness server Run API event stream: `turn_started`, `model_call` (with the HTTP Gateway's model-token usage and running total), `tool_call`, `tool_result`, `file_changed`, `tests_run`, `guardrail_blocked`, `review_submitted`, `plan_updated`, `issue_drafted` (one draft from a planning run's `create_issue`), `compacted`, `turn_finished`, `run_finished`. The meta-harness run service run activity stays the only consumer of each per-run sandbox's harness server Run API stream. Harness server Run API heartbeats are not stored. |
| Meta-harness run service: RunTask Temporal workflow | Built: `pull_request_opened`, `pull_request_checks` for each head commit's checks, `pull_request_reviewed` for each review round (round, verdict, reviewer, reviewed head SHA, review URL; rounds are numbered over the task's life, so a resumed task's, or a merge fix's, follow the ones it had, behind the `resumed-rounds` patch), `writing_checked` for the writing pass on each pull request description, review and comment it posts (the planner records it for a plan's issues), `pull_request_summary` for the summary each pull request description and fix round holds (fix round, source `run`, `recorded` or `files`, heading, text), `pull_request_merged`, and `merge_checks` for the merge commit's checks (merge SHA, state, failed check names, fix round). The task service records `budget_hold` (the reason) when a running task starts waiting on an operator for budget, and `budget_decision` (`raise` with the new limit, or `end`) for the answer. A review run's findings are in its `review_submitted` harness event. Planned: `pull_request_closed` while the workflow watches the pull request, `review_started`, `task_taken_over`, `task_handed_back` and `takeover_closed` with the operator as actor, and `operator_review_claimed`, `operator_review_submitted` and `operator_review_released` with the operator as actor |
| Pool manager | `lease_granted` (with `session_state`), `lease_released`, `session_saved`, `session_restored`, `sticky_handoff`, `sandbox_recycled`. The pool manager lease record names the per-run sandbox for each run. |
| Harness analyzer | `proposal_opened`, and operator approvals and rejections of harness analyzer proposals as audit events with the actor |
| Configuration, in the planner process | `config_changed` for every configuration version an operator saves, approves or rolls back, with the actor |

ai-api-unified records its own decisions in one `ai_api_request_cost` record
per request. The record lists every provider call the request made, failed
attempts and fallbacks included with their `fallback_reason`, and holds the
model that served the answer, `wasted_usd`, the router tier and method,
guardrail flags and cache hits, tagged with the caller id and the caller's
tags. In the meta-harness run service and the planner the library runs
in-process, and the calling service copies the record into its own event,
`task_routed` or `goal_classified`.

The HTTP Gateway writes each record to stdout, and Cloud Logging stores it. A
Cloud Logging log sink selects the HTTP Gateway records whose caller id is the
meta-harness and publishes them to a Pub/Sub topic. The event service receives
them on a Pub/Sub push subscription and appends each one as a
`gateway_request` event, joined to its run by the `run_id` tag the HTTP
Gateway sets from the run grant. Pub/Sub delivers at least once, so the event
service ignores a `request_id` it has already stored. The HTTP Gateway refuses
to start when its cost records would be lost, so every model turn of a run
reaches the event service.

| Decision | Made by | Event |
|---|---|---|
| A task's tier and candidates | The ai-api-unified router in the meta-harness run service | `task_routed` |
| The next candidate, an escalation, or the end of the loop | The RunTask workflow's Decide step | `attempt_finished` |
| Admitting or holding a task | The meta-harness scheduler | Admission decisions |
| A goal's modality and latitude | The planner, through the ai-api-unified classifier | `goal_classified` |
| Which drafted issues a plan keeps | The planner | `plan_drafted` |
| A turn's model, its fallbacks, guardrail blocks and cache hits | ai-api-unified in the HTTP Gateway | `gateway_request` |
| A review verdict | A meta-harness review run, or an operator | `pull_request_reviewed`, `operator_review_submitted` |
| A merge | The meta-harness run service, or an operator on the code host, which the planner's sweep records | `pull_request_merged` |
| Approvals, takeovers, cancels and configuration changes | An operator | Audit events with the operator as actor, `config_changed` |

The client library buffers events in its own outbox and retries, which is safe
because `append` is idempotent on `event_id`, so an event service outage never
blocks a run.

After an event service append commits, the event service
sends a commit notification, Postgres `NOTIFY`, with the run id and the new
highest `position`. The commit notification contains no meta-harness domain
event data and is internal to the event service: its listeners are the event
service's own `tail` handlers, which fetch the new event rows and stream them
to their callers. A missed commit notification only delays delivery, because
the next `tail` handler fetch catches up. `NOTIFY` fires only on commit; Redis
can replace it if the volume outgrows Postgres.

Each meta-harness producer service sets `seq` within its own
event stream. The event service assigns `position` on insert. Concurrent
Postgres transactions can commit out of `position` order, so the event service
`tail` handler re-reads a short window behind each caller's cursor and
suppresses repeats of meta-harness domain events it has sent. Delivery is at least
once, and a consuming service can drop a repeat meta-harness domain event by
`event_id`.

Consumers call:

| Consumer | Calls |
|---|---|
| Operator console | `read` for a meta-harness run's event history, then `tail` from the last position |
| Metering service | `tail` with one cursor per metering projection, into `model_call`, `attempt_usage`, `check_result`, `task_outcome` and `spend` |
| Harness analyzer | `read` for routing, attempt and run meta-harness domain events over time |
| Meta-harness alert rules | `tail` for stalled runs, failed meta-harness tasks with every routing tier exhausted, runs ended on `spend_limit`, and usage reconciliation gaps |
| Audit and export jobs | `read` for operator and configuration meta-harness domain events, and bulk export by time range |

The event service partitions its `event` table by time and by
each event type's retention class from the schema registry. Short-lived event
types (per-turn text, tool output) expire by dropping whole partitions rather
than deleting rows. The event service keeps run, routing, attempt,
`gateway_request` and audit meta-harness domain events long.

### Signals

| Signal | Source | Carried by |
|---|---|---|
| Meta-harness domain events | Every meta-harness service | Meta-harness event service |
| OpenTelemetry traces | One OpenTelemetry trace per meta-harness task, spanning routing, scheduling, each meta-harness run, each harness server Run API call and each HTTP Gateway call | OpenTelemetry; `task_id` and `run_id` on every span, and the trace id on every meta-harness domain event |
| OpenTelemetry metrics | Task queue depth, meta-harness scheduler admission decisions, per-run sandboxes by state, pool manager lease latency, run durations and turns per run. Model calls by routing tier, spend by Task API caller, HTTP Gateway latency and error rate | OpenTelemetry metrics |
| Operational logs | Operational text, structured, with `task_id`, `run_id` and trace id on every line; prompt and response content only when configured | Existing log pipeline |

### Console

Operators run and configure the meta-harness from the operator console, a
cloud web app. Operators sign in with Google at the edge, and the access
list names who may (see REST API). The operator console owns no store and calls only the REST API, as the signed-in
operator. A session lasts 12 hours. When one ends while a page is open, the
first request the edge answers `not signed in` takes the operator to the
sign-in screen, which says the session ended; signing in returns them to
the page they were on. Every error the console shows says what to do about
it: the page's own remedy where it knows one, otherwise reload to try again
and the Logs page for the warning that names it, and Sign in again for a
lost session.

Every view drills down to the next, and every list filters by goal, modality,
repo, time, harness profile, model, routing tier, task type and state.
Built today: the Overview, Goals and New goal, the Goal page, Tasks with
its terminal tiles, the Task page with its live run, and Configuration.
The Costs page (`console/src/features/performance`, at `/costs`; it was
named Performance, and `/performance` still leads to it) is what the
meta-harness costs over time. Four figures head it for the chosen range: the
total, the daily average, the change from the same number of days before
(no percentage when less than $1 was spent then, since a change from almost
nothing means nothing), and a month at the current trend. The controls start with the cost category: All costs,
AI (what model calls cost) or Cloud (the Google Cloud bill). Each category
has its own breakdowns and filters: All by category; AI by model, run type
(task runs, reviews, fixes, planning), goal, repository, operator, label
(the labels of the issue a task worked on, a task with several sharing its
cost evenly among them) or pull request outcome (merged, closed without
merging, still open, no pull request), with filters for model, run type,
goal, repository, label and pull request outcome, so what merged work cost
and what abandoned work cost are one choice each; Cloud by the service
billed, with a filter for it. A filter with a value always shows, so it can
be cleared, and its choices are never narrowed by its own value. Under them,
cost per day, week or month as stacked columns, one segment per value of the
chosen breakdown, with the trend line through
the totals and a tooltip listing every series of the hovered bucket. Beside
it, where the money went as a ranked list with each share, and the ten
costliest tasks, linked. Wherever costs are stacked or listed (the chart's
tooltip, the breakdown, the costliest tasks, a task's runs, the Overview's
most expensive tasks today), a total ends them. The range (7, 30 or 90 days, or two dates), the
bucket, the breakdown and the filters (source, kind of work, goal,
repository, model, label, outcome) live in the URL. A series keeps its color whatever is
filtered. An admin sees every source and is told what each is and when it
was last read; a regular user sees the model cost of their own work, and
the page says so.

Costs are gathered into one place by the cost service
(`src/meta_harness/costs`), whose routes and harvest loop run in the
planner's process for now, as the configuration API does. Every
`MH_COSTS_HARVEST_SECONDS` (300; 0 turns it off; checked at startup) it
reads each source into `costs.daily_cost`. Model cost comes from the event
service: each run's `attempt_finished`, and the planner's own model calls
(`goal_drafted`, `goal_titled` and `issue_sized`, each a planning cost of
its goal), read from the position the last harvest reached and saved with the new
position in one transaction, so an event is counted once. Cloud cost comes
from the Google Cloud billing export in BigQuery, by billed service; the
last 35 days, and back to the first of the previous month, are read again
each time, since a bill is restated and Google fills a new export from the
previous month. The page's Cloud category shows the bill by service. The billing
account's standard usage cost export writes to the platform stack's
`billing_export` dataset (turned on 2026-11-11), which the VM's service
account may read and query. `MH_BILLING_EXPORT_TABLE` names the dataset, and
the harvest finds the export's table in it, so the billing account's id is
named nowhere in the repo. Until Google has created the table, within a day
of the export being turned on, the page says the last read failed and why. Temporal runs on the VM, so its cost is inside Compute Engine's.
A source is a module with a `harvest` function. The edge names the caller's
role to the services (`X-MH-Role`), and the cost API decides by it who sees
what.

The edge forwards with its own service key, so it decides what a browser may
ask before it forwards (`src/meta_harness/edge/app.py`). It refuses a path
with an empty, `.` or `..` part (400): the role checks read the path as sent,
and the HTTP client collapses dot segments when it forwards. It refuses the
calls only a control-plane service makes (403): appending an event, and
setting a task's status, recovery or drive.

The services hold the same rules themselves, so the edge is not the only
check. The event service takes a control-plane service key on every route but
`/health`, and does not start without `MH_SERVICE_KEYS`; the event client
sends `MH_SERVICE_KEY`. The users routes, the configuration writes and the
kill switch's change refuse a request the edge marks as a regular user's
(`X-MH-Role`, `auth.admin_only`).

The header's inbox lists what waits on the operator: a pull request ready to
merge, a review that ended unresolved, a recent failed task, a goal over its
budget or stopped by it, a stalled goal, and the kill switch. It refreshes every 30 seconds and,
when the operator turned notifications on in Settings, raises a browser
notification for each new item.

Wherever the console shows failed work, it says what happened, why, and what
to do, with the action that does it, and never a bare count or a raw error
(`console/src/features/tasks/diagnosis.ts`). A failure's recorded reason is
read into a diagnosis: a summary, a cause, a fix, and the actions that apply
(Retry, Retry with a higher spend limit, open the task, edit the issue,
Configuration). The recorded error, without the wrappers each layer adds, is
shown when asked for. A reason no rule knows says what was recorded and what
to do next. Tasks that failed the same way are one message with one action
(`FailureGroups.tsx`): "Retry all 6", not six copies of an error. This is
what a goal's alert above its board shows, what the Overview's Needs you
section shows above its table, and what a failed task's own alert shows. Each
inbox item carries a line saying what to do; a pull request ready to merge
links to the pull request itself. The Overview's numbers are links, each with
a line saying what the number is made of: Needs you says how many failed, how
many are ready to merge and how many are held, and goes to the list.

A goal's Graph view draws the order of work: an issue sits to the right of
each issue it waits on, with an arrow from it, and says where it stands in
words (Queued, Waits on 2 issues to its left, Running now, In review, Done,
Needs you: failed, retry it). Color marks only what is done and what needs
the operator. A goal whose issues do not depend on each other has no order to
draw, and the view says so rather than stack boxes.

A value the operator may change is edited where it is shown, one way
everywhere (`console/src/components/InlineField.tsx`): hovering a value shows
a pencil, and clicking the value or the pencil turns it into its input (text,
number, a select, a switch, checkboxes, a tag list or a multi-line text area).
Enter or leaving the field commits it, Escape cancels, a multi-line field
commits with Ctrl/Cmd+Enter, and a switch or checkbox commits on change. A
refused value keeps the input open with the reason under it. Ids, dates,
versions, events, computed facts such as spend, and anything the API cannot
change stay read-only.

- Goal page: the title, description, budget, priority, parallel tasks and
  auto-merge (while the goal is open) edit in place, and each commit sends a
  PATCH of that field alone, one `goal_changed` record. There is no Edit goal
  dialog.

- Configuration: every value in a kind's view edits in place. Router tiers:
  each tier's name (a rename carries the default tier with it, as one
  change), description, thinking and candidates (added, removed and
  reordered), the classifier, the default tier and what happens when a tier
  is used up. Harness profiles: each profile's tools, max turns, max minutes,
  vCPU and memory, Bash allowlist (picked from the programs the profile's
  image has, `GET /v1/config/images/programs`) and image (picked from the
  images the deployment builds, `GET /v1/config/images`; empty is the default
  image),
  and a profile is added or
  removed from the table. Repo registry: each repo's tracker and default
  acceptance checks. Task policy: max parallel (with the VM's ceiling), fix
  rounds and the reviewer pairs, each a harness profile and a model picked
  from what the configuration has, as a tier's candidate is, and added,
  reordered and removed in place (`config/ReviewersField.tsx`; the last one
  cannot be removed). An edit is staged, not saved, because a
  version is checked as a whole and recorded with a note: a changed value is
  marked, and a bar above the view says "N changes to version V" with Discard
  and Review and save, which opens the diff and note and saves version V+1.
  A refused save shows each reason beside the value it is about (a profile's
  memory, a repo's checks, a tier's thinking or candidate; a reason about no
  one value shows above the view) and keeps the changes staged, and leaving
  the page with staged changes asks first, as does making another version
  active from the history: the staged changes are saved or discarded before
  the rollback. Save there opens the same diff and note review, and the
  leave or rollback goes ahead only once that reviewed save succeeds.
  Advanced: JSON opens the active document with the staged changes, and
  applying it stages what it holds, once its shape is one the view can show
  (a kind's lists are lists and its named entries objects); otherwise it says
  what is wrong and stages nothing.
- Repositories is a Configuration kind like the others, and the one place a
  repository is added, removed and edited
  (`console/src/features/config/ReposView.tsx`): Add repository chooses from
  the repos the GitHub App is installed on that are not listed yet, with a
  link to install the App on more; Remove and the default acceptance checks,
  a tag list, edit in place. Each stages, and all are reviewed and saved as
  one version. With no repository it says nothing can run until there is one.
- Settings: each field saves when it is committed and shows "Saved" beside
  it for a moment; there is no Save button. Saves run one at a time, each
  built on the settings the one before stored, so quick commits all persist.

The Task page's tab is in its URL (`?tab=`), so a link can name one and Back
returns to the one before; a link that names only a run (`?run=<run id>`)
opens the Terminal on that run alone, with Show every run to bring the rest
back. Each review round on the Reviews tab links to its own review run (Show
review run) and, when it sent findings to a fix run, to that fix run.
The Task page leads with its timeline. The task's facts (status and its
reason, repo, issue, pull request, acceptance checks, turn and spend) are folded
behind one line that keeps the link to its goal, and open with Show details; a
browser that leaves them open finds them open on the next task. The timeline's
first figure, the time from the task's start, is set large and counted by the
second while the task goes on, as the sign that it is running.

The Task page's Timeline tab, the one it opens on, is a picture of the
task's path, drawn from the task's events alone (`console/src/features/tasks/path.ts`
turns them into steps; no service works anything out for it). The five
stages are columns, left to right: Waiting, Run, Pull request, Review,
Outcome. Each step is a card in its stage's column, and time runs left to
right along a row. A step that belongs to an earlier stage than the one
before it starts a new row, with a line drawn back to it that says why
(`3 findings to fix`, `Held for budget`, `Sent back for its merge by the
supervisor`): so a loop shows as a return, round by round, and a task's
number of rows is the number of times it went back. Steps that share a
stage in one row stack in its cell: a second candidate's run under the
first's, the checks under the pull request, the merge commit's checks and
the task's end under the merge.

The code host runs the repo's checks on each new commit for several minutes,
and the workflow records nothing until they end. So while a task's newest step
is one that checks always follow (its pull request opening, a finished fix run,
its merge), the path adds a step that is still going: "Waiting for the pull
request's checks", or "Waiting for the merge commit's checks". A step still
going counts its time by the second, in the browser. The Terminal tab, which
prints nothing during such a wait, shows the same step and its time above the
terminal ("Now: ...").

While a task waits for checks, the console reads them
(`GET /v1/pulls/{task_id}/checks`, every five seconds while any is going, and
only while a page that shows them is open). The Checks tab lists each one with
its state, its time counted by the second, how long it usually takes, and a
link to its log. The waiting step, the line above the terminal and the tab's
name say how they stand ("4 of 6 passed, sandbox running 2m 10s, usually about
4m in all"). "Usually" is how long each check took on the default branch's
newest commit, kept ten minutes per repo. On a merged task, a check that did
not pass is shown as the same check on the default branch's newest commit when
that commit holds the merge (`planner/merge_checks.py`), the rule the task's own
merge wait applies: a newer merge cancels the checks still running on the one
before, and that one's deploy then fails for want of them, and neither is its
failure. Such a row says what it replaced ("Canceled by a newer merge"). The
tab says the checks run at the same time, a deploy waiting for the rest. The terminal prints a line when a
wait for checks starts and when it ends, with the result, and one for each
review round and for the merge. On a goal's board a running task's card says
what the task is doing now and for how long, from the same steps.

| Step | From | It shows |
|---|---|---|
| A wait: Queued, Held by the scheduler, Blocked, Held for budget, Held for an operator, Review taken by an operator, Queued again | `task_created`, `task_held`, `budget_hold`, `review_hold`, `operator_review_claimed`, `task_retried`, `task_driven`, `task_recovered`, a status change to `queued` or `blocked` | Its reason and how long it lasted, drawn with a dashed edge. A retry, and a task the supervisor sent back, name who did it |
| A run: Task run, Fix run | `run_prompt`, `run_finished`, `attempt_finished` | Its candidate, how it ended, its length, cost and turns. A run that did not finish is red, with why |
| Pull request opened, Checks | `pull_request_opened`, `pull_request_checks`, `pull_request_summary` | The pull request's number and files changed; whose words its description's summary is, as a note on the run that wrote it; the checks' result, the failed ones by name, and how long they took |
| A review round | the review run's events and `pull_request_reviewed` | Its round, verdict and number of findings, its reviewer, length and cost |
| Merged, Merge commit's checks | `pull_request_merged`, `merge_checks` | The merge commit, and its checks' result |
| Where it rests: Ready to merge, Review unresolved, Taken over | a status change to one of them | Why, how long it has rested, and what the supervisor asks of the operator |
| Its end: Closed, Failed, Canceled | a status change to one of them | Why |

What an operator did sits on the step it was done in: a message, a
dismissed finding, a raised spend limit, a review sent on. So does the
writing pass's record. The step the
task is in now is ringed and marked Now, and a step still going counts its
time up. A line above the picture sums the path: how long from start to
now or to its end, how much of that was waiting, its runs, review rounds
and cost. Hovering a step gives everything known about it. Picking a step
opens it under the picture: a run's turns one by one (each turn's message,
its model calls with model, tokens and cost, its tool calls opening to
their arguments and output, the files it changed, and the prompt it was
given, from its `run_prompt` event), with Open its terminal; a review's
round on the Reviews tab; the checks and the pull request on the code
host. A `?run=` link with `tab=timeline` opens with that run's step
picked. The picture keeps its width and scrolls sideways on a narrow
window. The page reads every step event of the task as it opens, besides
its newest events, so the path is whole however long the task has run,
and reads a picked run's own events when it is picked. The terminal is a
second tab; it takes every run's `run_prompt` as the page opens, as a
`prompt (<template>)` line heading that run's section, so an older run's
Terminal opens on its prompt. The prompt's full text shows expanded until
the operator collapses it to its line, and that run's prompt stays
collapsed in that browser after that. The Tasks page's terminal tiles read
each task's `run_prompt` events beside their recent events, so a tile
shows, expanded, the prompt of each run whose output it holds and of the
newest run, above that run's output, which scrolls past it as it grows. A
tile keeps those prompts apart from the 300 output lines it trims to, so a
long run's prompt stays at the top to scroll back to. A run started before prompts
were recorded shows none, and is told for a task, fix or review run by
its attempt's tier.

Three tasks from this repo's history, as the picture draws them:

```text
#252, merged after three fix rounds (1h 2m, 3s of it waiting, 8 runs, $8.13)

 WAITING     RUN                 PULL REQUEST        REVIEW                 OUTCOME
 Queued ---- Task run ---------- #257 opened ------- Round 1: 3 findings
 3s          11m 19s  $3.50      Checks passed 4m    51s  $0.19
             +---- 3 findings to fix ----------------------+
             Fix run ----------- Checks passed 4m -- Round 2: 2 findings
             6m 24s  $1.12                           54s  $0.22
             +---- 2 findings to fix ----------------------+
             Fix run ----------- Checks passed 4m -- Round 3: 2 findings
             8m 52s  $1.70                           46s  $0.17
             +---- 2 findings to fix ----------------------+
             Fix run ----------- Checks passed 4m -- Round 4: clean ------- Merged
             6m 17s  $1.05                           37s  $0.19             Merge commit's checks passed 7m
                                                                            Closed

#205, held, out of budget, refused its merge, sent back by the supervisor, unresolved

 WAITING              RUN                PULL REQUEST       REVIEW                OUTCOME
 Held by the          Task run --------- #258 opened ------ Round 1: 2 findings
 scheduler  1h 2m     20m 53s  $7.84     Checks passed 4m   51s  $0.23
                      +---- 2 findings to fix --------------------+
                      Fix run (red: spend limit)
                      4m 12s  $1.96
 +- Held for budget --+
 Held for budget ---- Fix run ---------- Checks passed ---- Round 2: clean ------ Ready to merge
 59m 44s              10m 12s  $2.39                        59s  $0.31            3h 10m: GitHub would
 an operator raised                                                              not merge it
 the limit to $15
 +- Sent back for its merge by the supervisor ---------------------------------------+
 Sent back for its merge ------------------------------- Review
 its pull request conflicts with its base                1m 26s  $0.46
                      +---- back for a fix ------------------------+
                      Fix run ---------- Checks passed ---- Review
                      (two more rows the same way, round 3: 1 finding)
                      Fix run ----------------------------------------------- Review unresolved  [Now]
                      3m 42s  $0.24                                           fix round 3 changed nothing

#263, held, then failed on its first run (6m 59s, 6m 49s of it waiting)

 WAITING              RUN                PULL REQUEST       REVIEW                OUTCOME
 Held by the -------- Task run (red) -------------------------------------------- Failed
 scheduler  6m 49s    9s: the HTTP Gateway refused the run grant                  Needs you: read why, then retry
```

While a task holds its newest review for an operator, the Reviews tab says
so above the rounds, each finding of that round has a Dismiss button that
asks why, and Send to fix now ends the wait. A dismissed finding shows
struck through, with who dismissed it and why, on any round. Such tasks are
in the Overview's Needs you list, marked "review waits". The Task policy tab
has the wait as "Wait before a fix run".
The Task page's Spend tab (#198) lists each run with its kind, model, turns,
tokens in, the share of them read from the cache, tokens out and cost, marks
the most expensive run, links each to its output, and heads them with the total, which also ends the table
against the task's limit and its split between the task's own runs, reviews
and fixes. On the Goal page, "where it went" beside the spend opens the
goal's: the split between own runs, reviews and fixes, and each task with
its runs, turns and cost, most expensive first, linked to its Spend tab, with the totals at the bottom.
An ended task's page offers what can be done with it, each button saying in
its tooltip what it does to the task and to the pull request: Retry (from
`review_unresolved`, `failed` or `canceled`, with a spend limit for the
retry), Merge (from `ready` or `review_unresolved` with an unmerged pull
request), and Mark done when its pull request merged or Cancel task when it
did not (both `POST /v1/tasks/{id}/clear`). There is no Clear task button.
A running task held for budget shows an alert at the top of its page with
what the next run needs and what is left, a field for a new spend limit
with Raise the limit and carry on, and End the task; one held for its goal's
budget has a field for the goal's new budget instead, holding the budget a
resume would set, with Raise the goal's budget and carry on, a link to the
goal, and Look again now. The scheduler hands an answer to the task's
workflow at its next pass, so once one is accepted the alert gives way to a
line saying what was sent ("Limit raised to $15.00. The task continues at its
next step."), and the page reads the task again every few seconds
(`console/src/features/tasks/budgetAnswer.ts`). The workflow writes the task
once it has taken the answer, which moves its `updated_at`: the line stays
until then, however long the scheduler takes, and clears when the task waits
no more. When the workflow held it again, the alert comes back with the
reason it wrote. Money reads as dollars and cents everywhere: the
services round the amounts in the reasons they write
(`src/meta_harness/money.py`), and the console formats every amount it
shows and rounds the amounts in a reason recorded before that
(`console/src/money.ts`). Held tasks head the
Overview's Needs you list, and show under Needs attention on their goal's
board and in its alert.
A queued or blocked task says why it has not started in a Waiting to start
notice at the top of its page (`console/src/features/tasks/WaitingToStart.tsx`):
the scheduler's reason and when it last looked, with the action that fits
the hold's kind. A goal over budget offers the goal's Resume and a link to
its budget; a goal that is paused, planning or closed shows its status and
links to it; a parallel limit links the tasks holding the places; a repo
area names the task holding it and the paths they share; the kill switch or
a drain links to the Overview. A blocked task lists each issue it waits on
with its state; one whose goal is canceled or met says that first instead,
with a link to the goal, since it will not start whatever it waits on. A queued task nothing holds says it starts at the
scheduler's next pass. The Timeline shows a hold as a wait, with its reason
and how long it lasted until `task_admitted`, the Tasks page's Queued and blocked list has a Why it waits
column, and a goal's board shows the reason on a queued task's card.
A task that ended `failed` or `review_unresolved` says so in an alert at
the top of its page: its reason, each run that did not finish (linked to
that run), and a link to its warnings and errors on the Logs page. Its
goal's page shows an alert above the board listing each failed or
unresolved task with its reason, linked to the task, and each issue the
planner refused.
New goal chooses a goal's issues in one place: its Label field and Pick
issues. A label, typed or set by a draft, selects every open issue with it in
the goal's repos that no other goal holds: the form lists them under the
field, and Pick issues shows them checked and locked, marked "via label",
since the label takes them whatever is unchecked; to leave one out, the
operator clears the label and picks by hand. Issues picked by hand are sent
as the goal's `refs`; the label's are not. The picker's Label filter suggests
the labels of the repos it shows, with their open-issue counts, as the Label
field does. Above the form, "Draft from a label (optional)", closed until
asked for, lists a label's open issues read-only and has a model draft the
title and description from those no other goal holds; it fills the form and
saves nothing.
New goal asks where the goal's issues come from: issues that exist (a `fix`
goal, by label and by picking), or planned from the description (a `feature`
goal, whose label is optional). On a goal in `planning`, the Goal page shows
its plan (#75): while the planning task drafts, a notice with a link to its
run, reread every 5 seconds; then each draft as a card, in the order the
work can be done, with its repo, title, body and what it waits on, its
acceptance checks set apart, and above them the order of work as a small
graph of which draft follows which. The page says which version is shown and
who made it, and links to the planning task's run. A draft is edited, added
or removed in a dialog; edits are held on the page until Save as version
N+1, and leaving with unsaved edits asks first. An invalid plan lists its
reasons and cannot be approved. Approve the plan says how many issues it
will create and with which label, then creates them and starts the goal.
Replan takes a note and starts a new planning task. The body shows as text,
not rendered Markdown.
The Goal page keeps itself current while it is open and the goal is not
closed: it rereads the goal and its issues every 5 seconds (not while the
browser tab is hidden, and at once when it shows again), besides listening
to the live stream of the tasks it knows, whose list each reread renews. An
issue with no task yet has nothing to listen to, so without the reread a
started goal's cards stayed in Queued. Each of its buttons rereads the board
at once, and the section's header says when it last refreshed, or why a
reread failed, without clearing what it shows. A card with no task yet shows
its issue's title, which the planner stores on the issue link when it takes
the issue in and refreshes on each sweep.
The Goal page shows an active goal's issues as a board, columns Queued,
Running, In review, Done and Needs attention (a failed task, failing
checks, a refused issue, an issue the planner held for its size or its gaps,
or a failed dependency), each card with its task's
status and cost, its issue's size (its reasons on hover) and what the issue
is missing, and its pull request with its checks. A held card says why, with
the suggested split, and offers Run anyway and Split, whose dialog lists
the suggested issues as drafts to edit and create, each with the goal's label
and a `Paths:` line (`console/src/features/goals/sizing.tsx`). A Graph view lays the
same issues out by dependency, colored by column, and a Table view lists
them. Issues done and spend against budget head the section, and the page
reloads them as the goal's tasks record events. Past the budget the spend
line says where the goal stands ("$10.40 spent of $10.00: over budget, stops
at $11.00", or "over budget, and it keeps going" for a goal that only warns)
and its bar turns orange; stopped, red. A goal over its budget or stopped by
it shows an alert at the top of its page, is marked in the Goals list, and
heads the Overview's Needs you list. For a goal that stops, the alert, the
page's actions, its Needs you row, and the page and Needs you row of a task
held for its budget offer Resume: "Raise the budget from $10.00 to $45.00",
the amount editable before confirming (`console/src/features/goals/budget.tsx`).
Each field the operator set on a goal edits in place on the Goal page, with
no dialog: the title (the page heading), the description under it, and in
its facts the label (with New goal's suggestions, cleared only when the goal
holds picked issues), the repos (checkboxes of the registered repos, at
least one), the budget (cleared to None), which shares its row with, for
a goal with one, what happens when it is reached (Stop, or Warn and keep
going) and for Stop the allowed overage in percent, the priority, the parallel tasks
(empty for the system setting, no more than it), auto-merge, a switch, and
strict issues, a switch.
A click on a field, or on its pencil icon on hover, turns it into its input,
with New goal's limits; Enter or leaving it saves, Escape cancels, and the
description saves with Ctrl/Cmd+Enter. A save sends only that field
(`PATCH /v1/goals/{id}`), shows that it is saving and then the saved value;
a refused save keeps the input open with the API's reason under it. The
modality, the goal's id, Created, Started and Ended are read-only, and a met
or canceled goal shows every field read-only until it is reopened. Cancel
goal asks first (`console/src/features/goals/CancelGoal.tsx`): its dialog
says how many tasks that have not started are canceled, how many running
ones are stopped, and how many have a pull request open and are left, with a
link to each. The page of a goal no sweep covers says when its issues'
states were read from GitHub. The rest
of the table is planned.

The console's left pane lists the plain pages first (Overview,
Costs), then Goals and Tasks, each opening to its history
(`console/src/components/NavTree.tsx`). Goals lists the open goals, the first
ten with a link to all of them, and the met and canceled ones under a closed
"Past goals" row, the first twenty with a link; each goal opens to its tasks,
read only once it is opened, and the goal whose page is showing starts open.
Tasks lists what is in progress across every goal, the tasks an agent is
working on and the ones whose pull request waits on someone, the first ten
with a link to the Tasks page; a task that has not started is under its goal.
The closed, failed and canceled ones are under "Past tasks", fifty at a time
with Show more. Every row has a dot in its
status's color, and the lists read again on each page change and every 30
seconds; a list that could not be read says why and to reload. The admin
pages are one Admin entry at the bottom of the pane, under New goal: a click
opens them in a menu beside the pane, and the entry is highlighted while an
admin page shows (`console/src/components/AdminMenu.tsx`).

The kill switch is on every page (`console/src/components/KillSwitch.tsx`).
An admin has a Stop everything button in the header; it asks first, with an
optional reason that is recorded. While the switch is on, a red banner over
every page says nothing runs, who turned it on, when and why, and the header
button and the banner lift it, after asking. While a deploy drains, a yellow
banner says so; an admin may end the drain by hand, and is told to do so only
if no deploy is running. Someone who is not an admin sees the banner and that
an admin can lift it. Nothing shows while the switch is off. The console reads
the switch every 15 seconds.

Router tiers are added, removed and put in order in the view
(`console/src/features/config/TiersView.tsx`), which opens by saying what a
tier is and why the order matters. Add a tier puts one last, named so no two
share a name, with no candidate yet; each tier has earlier and later
buttons, each move one staged change, and Remove, which the default tier
does not offer until another tier is the default.

An action that stops or ships work, or cannot be taken back, asks first
(`console/src/components/ConfirmButton.tsx`): its dialog says what will
happen and what will not, the action runs only from the dialog's own button,
and a failure keeps the dialog open with why and that nothing changed.
Cancel task (a working task's run is stopped and its unpublished changes
stay in its workspace; one not started is canceled), Merge, Mark done, an
ended task's Cancel, Clear selected on the Tasks page, and Complete goal
(running tasks go on, unstarted ones stay queued) all ask, as Cancel goal,
Remove from goal and the kill switch already did. The button that raises a
goal's budget reads "Raise budget"; "Resume" is only a paused goal's.

The New goal form goes in the order a goal is thought of: title and
description; its repositories; its issue tracker, always said, as a choice
when more than one is set up and as plain text when one is, with a warning
when it has no sign-in, then what that tracker asks of a goal; then where its
issues come from. Under "Issues that exist" sit the label, the picked issues
and the helper that drafts a goal from a label, with "it needs at least one
of the two"; under "Plan them from the description" only an optional label.
A goal that could not be saved says why beside the Save buttons, with where
in the form to fix it and that nothing was created. How a goal gets its
issues has one name on every page, the form's own: "Issues that exist" or
"Planned from the description" (the service's word, modality, is never
shown), and the goal page says it and the tracker were fixed when the goal
was created. A person's Settings hold what the form starts from, in its
words, and nothing the form ignores.

A value that can be edited shows a pencil, dimmer until pointed at. One
component draws every such value (`components/InlineField.tsx`): text, a
number, a choice, tags, a switch, checkboxes, or `custom`, a value with an
input of its own, which a goal's fields use (`goals/InlineEdit.tsx` adds only
the save to the API, whose refusal becomes the reason under the input). So a
value opens, commits (Enter or leaving it), cancels (Escape) and shows a
refusal the same way in Configuration, Settings and on a goal's page. In
Configuration, where edits stage, the bar that says so stays in view as the
page scrolls and reads "N changes not saved yet. Nothing applies until you
review and save"; every value, row or item not yet saved carries the one
marker "not saved". The review lists each change in words by where it is
("tiers › fast › thinking: low to high", "repos › o/a: removed"), one line
for each change counted (`staging.describeChanges`), with the JSON behind a
button. A save the service refuses keeps the review open with its reasons and
"Your changes are still here"; the reasons are beside their values too.
A removed row leaves its view at once, so the view lists it under the table
as "Removed, not saved yet" with Undo (`config/RemovedRows.tsx`): a
repository, profile or skill is staged back as the active version has it,
and a tier or a tier's candidate goes back in the place it had. A tier's
and a candidate's removal is remembered when it is made, since a rename also
takes a name out of the document, so their Undo lasts while that view stays
open; Discard still drops every staged change.

A new deployment says what it needs before any work runs
(`console/src/features/overview/GettingStarted.tsx`): on the Overview, four
steps in order, each ticked once it is done and linking to where it is done:
add a repository, check the default issue tracker is signed in, create a
goal, give it an issue. The next step has its button; a step only an admin
can do tells anyone else to ask one. Once all four are done the checklist is
gone. An empty list says why it is empty and what fills it, with a link
where there is one: each Tasks tab, the Goals list, a task's timeline, the
Audit, Users and Logs pages, and Skills.

A section that could not read what it lists says so, with why and what to do
(`console/src/components/ReadFailed.tsx`): it never shows nothing, or its
empty state, in place of a failure, so "none" means there are none. An
action that failed says it did not happen and that nothing changed. The
shell tells an account that is refused (ask an admin for an invite, or sign
out and use another) from a console that cannot be reached (try again).

A status has one label, one meaning and one color, wherever it shows
(`console/src/components/status.ts`): the service's names never reach a page.
A task reads Queued, Blocked, Working, In review, Taken over, Ready to merge,
Review unresolved, Done, Failed or Canceled; a goal Planning, Active, Paused,
Not started, Stalled, Out of budget, Met or Canceled. A badge says its
meaning on hover and to a screen reader. The same holds on a task's Reviews and
Attempts tabs (`features/tasks/words.tsx`): a review's verdict reads Clean or
Changes asked for; an attempt's ending reads Finished, Did not finish, or Did
not finish, the last try; who reviewed or ran it reads as its model and its
harness profile, with the pair as the configuration writes it on hover; a
review round says when it was made and at which commit; and an attempt that
did not finish says what happened in words where the console knows the
recorded reason, with the reason under it. A working task with a pull request
open is In review on its page, in every list and in the left pane.

What needs the operator is one list with one count
(`console/src/features/overview/needsYou.ts`): the tasks the supervisor found
an operator to have, and the open goals past their budget or with nothing
moving. Each item leads with what it asks for ("Merge", "Retry or fix",
"Raise the budget", "Decide on the review"), then says why and what to do.
The Overview shows all of it directly under its tiles, the failed and
unresolved tasks grouped by what went wrong and the rest one row each, with
Raise budget inline; the header's inbox shows its count and first six with a
link to the rest. Nothing ages out of it. An agent's review in progress is
listed under the inbox as "Also happening" and is not counted, and the kill
switch has its banner. A read that fails says so and keeps the list as last
read: an empty list means nothing needs the operator.

Each thing is set in one place. A page never shows a setting it cannot
change that another page can: there is no Repositories or Kill switch page
under Admin (their old addresses lead to Configuration, Repositories and to
the Overview), and a person's Settings page does not repeat the deployment's
trackers.

| View | Shows | Actions |
|---|---|---|
| Overview | Every active goal with its modality, progress against its success criteria, spend against budget and tasks in flight. A "needs you" list: plans and phases to approve, operator issues (releases, deploys) only an operator can close, unresolved reviews, alerts. Run capacity and HTTP Gateway health | Create goal |
| Inputs | The queued GitHub issues across every registered repo, by repo and by goal: each one's state (queued, blocked and on what, running, pull request open, in review, merged, failed), issues no goal has picked up, and which repos each goal is working in now | Add an issue to a goal, drop one |
| Goals | Each goal with its status, its cost, and its budget with a bar for how much of it is used and "12% used", "over by $4.10" or "stopped at its budget" ("no budget" without one); a cost that could not be read shows unknown, with why on hover. Sorted newest first, most expensive first or most budget used first, with the listed goals' total cost below, and their total budget when every one has one. Badges show their whole text | Open a goal, start a new one |
| Goal | The goal's statement, its modality (with the classifier's confidence, or "set by operator") and latitude dial, its issue dependency graph across repos, the plan or the phase timeline with each phase's criteria, every success criterion's live state, spend over time, and its tasks. Each planner-drafted issue shows its `latitude_reason`. A goal saved without starting shows Not started, with its label's open issues read but not yet taken in | Start a saved goal, approve plan or phase, set latitude, edit its title, description, label, repos, budget, priority, parallel tasks and auto-merge in place, pause, resume, cancel |
| Tasks | One task: its issue, every ai-api-unified router routing decision, every run including fallbacks, every check result, the diff, the pull request, each review round's verdict and findings and the fix run it led to, the merge, and cost per run | Take over, hand back, cancel |
| Run | Live timeline of the run's events: each turn's model call (model, thinking, tokens, cost, latency) and its ai-api-unified decisions (the model that served it, fallbacks, guardrail blocks, cache hits), tool calls, file changes, test runs, compactions and guardrail blocks, the OpenTelemetry trace, and the per-run sandbox | Take over, cancel |
| Live runs | Every run in flight across every goal: task, repo, model, turn, spend as it accrues, time since its last event | Take over, cancel |
| Overview's Pull requests row | Every pull request of the last 13 weeks as one strip of cells, oldest first, colored by state (blocked and waiting strongest), with how many are blocked and waiting and a count of each state beside its color. There is no Activity page: where the work and the money went, by goal, repo, label or pull request outcome, is the Performance page's | Open a cell's latest run (`/tasks/{id}?run=`) |
| Routing | Tier picked versus the candidate whose run finished, classifier confidence, floors and ceilings by modality, moves between candidates and escalations | |
| Pool | Per-run sandboxes by image and state, warm targets versus actual, lease latency, health failures | |
| Spend and performance | Cost by goal, modality, repo, model, harness profile and routing tier. Cost per merged pull request and time to merge, by modality. Dollar-hours per completed task over time. Cache hit rate | |
| Configuration | The active routing tiers, harness profiles, modalities, task policy and repo registry, their version history, and the harness analyzer's pending proposals | Edit any value in place, staged and saved as one reviewed version; roll back a version, approve or reject a proposal |
| Audit | Every recorded decision, by a meta-harness component, ai-api-unified or an operator, filtered by who made it | Export a time range |

A task's runs, fallbacks and retries included, run one after another, and the
Tasks view lists them in order with the task's review and fix runs under
them.

The operator console reads runs from the event service: `read` for a run's history,
then `tail` from the last position, so a run in progress opens at any point
with no gap, and a slow operator console falls behind without slowing a producer. Tool calls, tool results, file changes, test runs,
guardrail blocks, compactions, model tokens and spend are live as they
happen, from the `model_call` events recorded off the harness server Run API.
Model text arrives once per turn, and token by token once the HTTP Gateway
streams.

Event payloads contain repository content. The operator console
scrubs secrets from the events it reads. The HTTP Gateway's per-turn
redaction applies to what the model receives.

### Alerts

Meta-harness alert rules tail the event service and read OpenTelemetry metrics.
They fire on: a meta-harness run stalled past its harness server heartbeat
window, pool manager lease TTL expiries, warm sandbox pool below target for
longer than the per-run sandbox provisioning time, HTTP Gateway error rate or latency
past its SLO, a meta-harness task failing with every routing tier exhausted,
a meta-harness task ending `review_unresolved`, a goal `stalled`, a claimed review round
waiting on an operator past `review.operator_alert_minutes`, spend past 80%
of any cap, usage reconciliation gaps over 5%, and a harness
profile and model whose cache hit rate falls below its configured floor.

## Security

- An operator installs the meta-harness GitHub App on each registered repo. A
  per-run sandbox gets a
  read-only, repo-scoped access token for its clone, minted per run and
  expiring within an hour. The meta-harness run service writes to the code host with the
  app's own token: it pushes branches, opens pull requests, posts reviews and
  merges, and the planner creates and updates issues.
- The Publish step runs git on the host against a workspace pi could write
  to. The clone's git directory is outside the sandbox mount and read-only
  inside it, and host git names that directory outright, so pi cannot make
  the host run a hook or a configured command with the control plane's
  credentials.
- Secret Manager mounts each secret only into its owner service's
  pods. The HTTP Gateway holds the model provider API keys for every model call
  a run makes. The meta-harness run service and the planner hold one more: a classifier
  provider API key for the ai-api-unified classifier model only. A provider project limited to that model issues it, with its own spend
  limit, so a leaked classifier provider API key exposes classification and
  nothing else.
  A per-run sandbox holds only a run-scoped run grant key and a read-only
  access token, both short-lived. Prompts, task specs and logs carry no
  secret.
- Task descriptions, meta-harness review findings, CI logs and
  every tool result pass ai-api-unified's prompt-injection and tool-result
  guardrail checks at the HTTP Gateway, and
  the meta-harness task prompt template places them in a delimited data
  section.
- Each per-run sandbox runs under gVisor with a default-deny NetworkPolicy and
  the Secure Web Proxy allowlist, and holds no model provider key, cluster
  credential or Google Cloud permission. The harness server is
  the only process in the per-run sandbox that talks to the meta-harness
  control plane, over mutual TLS.
- Every meta-harness task launch, review or fix run, operator
  review claim and verdict, takeover, merge, cancel and configuration change is a meta-harness domain event in the event service,
  recorded by the meta-harness service that received it, with its actor.

## Deployment

The meta-harness runs on Google Cloud, one project per environment. Workload
Identity gives each managed service one owning component. The ai-api-unified
library runs in-process in the meta-harness run service, planner and HTTP
Gateway pods.

```mermaid
flowchart TB
    USERS[Operators and REST API callers]
    CH[Code host]
    REG[Package registries]
    PROV[Model providers]
    TC[Temporal Cloud<br/>run service Temporal namespace]

    subgraph GCP["GCP project, one per environment"]
        XLB[External HTTPS LB<br/>Identity-Aware Proxy for the REST API and operator console,<br/>path routing to owning services, webhook routes without IAP]
        subgraph VPC["Private VPC"]
            subgraph GKE["GKE Autopilot cluster, private"]
                subgraph CTRL["namespace: control, one Deployment and HPA per meta-harness component"]
                    TS[meta-harness task service<br/>Task API]
                    PL[meta-harness planner]
                    SCHED[meta-harness scheduler]
                    RS[meta-harness run service Temporal workers<br/>ai-api-unified in-process]
                    PM[meta-harness pool manager]
                    EV[meta-harness event service]
                    MET[meta-harness metering service]
                    HA[meta-harness harness analyzer]
                    CON[meta-harness operator console]
                    GW[ai-api-unified-http HTTP Gateway<br/>ai-api-unified in-process]
                    OTEL[OTel collector DaemonSet]
                end
                subgraph SBX["namespace: sandbox, gVisor runtime class"]
                    POD[per-run sandbox pods with harness server and pi coding agent<br/>one per run, pool manager warm pool unleased]
                end
                GPU[optional GPU node pool<br/>vLLM local models]
            end
            ILB[Internal HTTPS LB<br/>for the ai-api-unified-http HTTP Gateway]
            SWP[Secure Web Proxy<br/>per-run sandbox egress allowlist]
            NAT[Cloud NAT]
            SQL[(Cloud SQL for PostgreSQL<br/>private IP, schema and role per owning component)]
            MEM[(Memorystore for Redis<br/>key prefix per owning component)]
        end
        GCS[(Cloud Storage<br/>bucket per owning component)]
        AR[Artifact Registry]
        SM[Secret Manager]
        OBS[Cloud Logging, Trace, Monitoring]
        PS[Pub/Sub topic<br/>ai-api-unified request records]
    end

    USERS --> XLB
    CH -->|code-host webhooks, signature verified by the task service| XLB
    XLB -->|/v1 paths, each to its owning service| TS & PL & RS & EV & MET & HA & PM
    XLB --> CON
    PM -->|Kubernetes API: create, delete per-run sandbox pods| POD
    RS -->|harness server Run API, mTLS, pod address from pool manager sandbox lease| POD
    POD -->|pi model turns, HTTP Gateway run grant key| ILB --> GW
    GW -->|optional local models served by vLLM| GPU
    GW --> NAT --> PROV
    POD -->|code-host clone, push, package downloads| SWP --> NAT
    NAT --> CH
    NAT --> REG
    RS -->|Private Service Connect to the run service Temporal namespace| TC
    CTRL -->|each meta-harness component through its own Cloud SQL role| SQL
    CTRL -->|each meta-harness component under its own Redis key prefix| MEM
    CTRL -->|each meta-harness component to its own Cloud Storage bucket| GCS
    SM -->|secrets mounted only into the owning component's pods| CTRL
    AR -->|container images| GKE
    OTEL --> OBS
    GW -->|ai-api-unified request records on stdout| OBS
    OBS -->|log sink: HTTP Gateway records with the meta-harness caller id| PS
    PS -->|push subscription| EV
```

### Deployment without GKE

The deployment that exists runs the whole control plane on one Compute
Engine VM and the console's edge on Cloud Run. GKE and the pool manager
replace the VM behind the same meta-harness components when runs outgrow one
machine. The cloud is declared in Terraform, under `deploy/terraform`, as two
stacks with state in a Cloud Storage bucket.

| Component | Runs on | Notes |
|---|---|---|
| Edge: the console web app and `/v1` routing | Cloud Run, behind Firebase Hosting at `https://<console_site>.web.app` (`deploy/environment.json`, a local file the repo does not hold) | `deploy/edge.Dockerfile`. The edge signs operators in with Google itself (see REST API), checks the access list, then reaches the VM's private address over Direct VPC egress |
| Task service, planner, event service, meta-harness scheduler | The VM, as systemd units under the `mh` user | `deploy/vm/units`. Each listens on a private port that only the edge's subnet reaches |
| Postgres | The VM, in a Docker container, its data on the data disk (`/var/lib/meta-harness/postgres`) | `mh-postgres.service`. One copy on one disk; Cloud SQL replaces it |
| Temporal | The VM, the Temporal dev server with its history in one file | `mh-temporal.service`. A development server, one process, no replication. Temporal Cloud replaces it before runs that matter |
| Redis | The VM, the distribution package | The HTTP Gateway's run grants and per-run spend counters. The scheduler's locks and kill switch are Postgres rows |
| HTTP Gateway | The VM, from a checkout of ai-api-unified-http, reading its own `config/models.json`. Its guardrails are off, as that file ships | `mh-gateway.service`. Bound to every interface so the egress proxy container reaches it; no firewall rule admits its port from outside the VM. The meta-harness `models.json`, whose router names `harness` candidates, reaches the worker alone: the gateway's completions factory refuses a `harness` engine |
| Meta-harness run service worker, egress proxy, per-run sandboxes | The VM, with the Docker executor backend | `mh-worker.service`, `mh-egress.service`. Per-run sandboxes are containers on the VM, and `MH_MAX_SANDBOXES` caps them at 8, the ceiling on the task policy's `max_parallel`: the VM, an `e2-standard-16` (16 vCPU, 64 GB), holds eight sandboxes at their profiles' 6 GB cap and 16 GB for everything else. Each run's workspace and git directory live under `/var/lib/meta-harness/runs` |
| Logs | The systemd journal, every `mh-*` unit's output, shipped to Cloud Logging as `mh_journald` by the Google Cloud Ops Agent (major version 2, `deploy/vm/ops-agent.yaml`), which the boot installs. A failed install leaves the logs in the journal and stops nothing. Every service logs through `meta_harness/logs.py`, which under systemd starts each line with its journal priority (`<4>` warning, `<3>` error), so a record's level becomes its Cloud Logging severity; without the prefix every line arrives as INFO. Failures are logged at WARNING or ERROR, each line naming `task <id> run <run id>`: the RunTask workflow logs a run that did not finish, a review run with no verdict, a merge GitHub refused or a conflict, and failed checks on a merge commit as warnings, a task that ends `review_unresolved` or unreviewed as a warning, and one that ends `failed` as an error; the task service logs every move to `failed` (error), `review_unresolved` or `canceled` (warnings), whoever set it | The console's Admin menu reads them through `GET /v1/admin/logs`. The Logs page opens on warnings and above, and takes its search and range from its link (`?q=<task id>&minutes=1440`) |
| Secrets | Secret Manager, read at boot into `/etc/meta-harness/env` | Provider keys, the service key, the gateway key, the repo registry and each GitHub App's private key. 

The **platform** stack is what changes rarely: the network, Cloud NAT, the
firewall rules, the VM's fixed address and its data disk, the service
accounts and every role they hold, the GitHub sign-in, the secrets'
containers, Artifact Registry and the console's access list. The
**release** stack is a commit: the VM at that commit, and the edge on Cloud
Run from the image built from it.

Every merge to main deploys itself, infrastructure included, so a task's
merged pull request is all it takes to change either stack.
`.github/workflows/deploy.yml` waits for main's five checks to pass at the
merged commit, then:

1. It plans the platform stack. No change applies nothing. A change is
   applied, unless `deploy/platform_guard.py` refuses the plan.
2. It builds the edge image and pushes it to Artifact Registry tagged with
   the commit.
3. It applies the release stack with the commit and the image. The release
   stack reads the platform stack's outputs, which is why the platform goes
   first: a pull request can add a platform output and use it in the release
   stack at once.
4. Once the VM reports the commit, it runs a smoke task through the deployed
   system (#204, `src/meta_harness/smoke.py`): one small task, submitted
   through the Task API on the VM as a caller would, that writes a one-line
   file in a scratch repo on the VM, pinned to the first candidate of the
   router's first tier, with a $0.25 spend limit. The scheduler admits it
   whatever is running, the RunTask workflow runs it in a per-run sandbox,
   with its database, against the real HTTP Gateway, and it ends `closed`.
   Its type is `smoke`: only a service creates one, it opens no pull request,
   gets no review, never holds for budget, and the console's lists leave it
   out (`GET /v1/tasks?smoke=true` lists them). A task that does not end
   `closed` in ten minutes is tried once more, since one model call can fail
   by chance; two failures fail the deploy, and its failure issue quotes
   what the smoke task said. While the kill switch is on or the scheduler
   drains, no task may start, and the smoke task is skipped. Tests and the
   zero-cost end-to-end check use fakes for the model, the code host and
   the VM; only this shows whether a task runs on the deployment.

The guard refuses a plan that deletes or replaces what the deployment cannot
lose: the data disk, the network, a service account, the sign-in from
GitHub Actions, a secret, the image repository, the console's site, an
enabled API, or the deploy account's own way in (its sign-in and its access
to the Terraform state). New resources, changes in place, and removed
grants and firewall rules apply. A refused plan fails the deploy before
anything changes, and its issue names the resources.

The workflow signs in as `mh-deploy-sa` through workload identity
federation, whose provider accepts only this repository's main branch, so
GitHub holds no Google Cloud key and a pull request's code never runs with
cloud access. Applying the platform stack takes roles that let the account
change IAM, secrets and the network, so the account can grant itself
anything in the project. What guards it is the path to main: the checks, the
review loop's review on another model, and the guard on the plan. No pull
request sees its plan before it merges, because planning needs cloud access
that a pull request must not have.

Three things stay outside a deploy. A secret's value is added by hand
(`gcloud secrets versions add`), since Terraform never sees one; a secret
the system can generate itself, as it does the session key, belongs in the
stack. A new project is set up once by hand (`make platform`), which is also
how a plan the guard refuses is applied. And a failed platform apply has no
rollback: the fix is the next merge.

A deploy updates the running VM in place. The release apply changes only
the VM's metadata (the commit, the startup script), so the VM keeps running.
The workflow then runs the startup script on it over IAP SSH, as the deploy
account through OS Login, inside a `systemd-run` unit so a dropped connection
can't stop it. The script checks the commit out and runs
`deploy/vm/boot.sh` in update mode:

- Postgres and Temporal keep running, and migrations apply beside the
  running services: the script runs `mh migrate` itself and never restarts
  the `mh-migrate` unit, which every API service requires, so restarting it
  would stop them all at once.
- The sandbox images are rebuilt only when their sources changed.
- The egress proxy and the HTTP Gateway restart only when they changed,
  because a restart drops a running sandbox's model calls.
- The control-plane services restart one at a time, each answering
  `/health` before the next. A service gives its open requests five seconds
  on a stop, and systemd ends it after fifteen (`TimeoutStopSec`), so a
  connection a client keeps open, such as the console's live event stream,
  cannot hold a restart.

Running sandboxes are Docker containers that outlive the restart. The
interrupted run activity ends with `WorkerShutdown`; its workflow waits for
the restarted worker and runs the activity again for the same run, which
re-attaches to the same container, so no running task is lost. A workflow
used to take that failure as the run's own and fail the task for good. The edge
retries a request whose connection is refused for up to 20 seconds, so a
service's few seconds down don't reach the console. Old and new code run
side by side for those seconds, so migrations only add, and anything removed
goes in a later pull request.

The restarted worker also replays every running RunTask workflow's history
against the new workflow code. A change to which activities, timers or
signals a workflow runs, or their order, no longer matches a history
recorded before it, and Temporal retries that workflow task without end:
the task neither finishes nor fails. So a change like that goes behind
`workflow.patched("<change-id>")`, and a later pull request removes the old
branch with `workflow.deprecate_patch` once no workflow started before the
change is still running. `tests/run_service/test_replay.py` replays recorded
RunTask histories (`tests/run_service/histories`, Task API tasks through the
review loop) against the current code on every pull request, so a change
that would break running workflows fails before it merges.

A failed update rolls back. When any step of an update fails (a service that
does not answer `/health` within 60 seconds, a migration, a dependency
install), `rollback` in `deploy/vm/services.sh` checks out the commit the
update replaced, reinstalls its dependencies and units, and restarts every
service the update had restarted, one at a time with the same health checks.
The services after the failed one never ran the new commit. Because
migrations only add, the previous code runs against the new schema. The
guest attribute reads `failed <commit>: rolled back to <previous>`, or
`failed <commit>: rollback failed, a service is down` when the previous
commit doesn't come back healthy either. The deploy fails, and opens an issue
labeled `meta-harness:operations` that names the commit, so a goal can fix
the change. CI checks a change before it merges; the rollback covers what
only shows on the VM, such as a migration against production data, a
setting checked at startup, or a missing secret. Without it, a merge that
broke the worker would leave nothing to run the fix.

A deploy whose commit failed one of main's checks never starts. It opens an
issue titled "blocked: a check failed on main" that names each failed check
and links its run, and nothing on Google Cloud changes.
A deploy is a check run on the merge commit, so the task whose pull request
merged waits for it (Review loop, the merge): a failed deploy also becomes
that task's fix run, besides the issue it opens.

Terraform replaces the VM only when its definition changes (boot image,
disks), and stops, resizes and starts it for a new machine type. The first
deploy to a project creates it. In each case the VM boots by itself, and the
workflow runs no update over SSH on top of that boot: its startup script mounts the data disk, which carries Postgres data, Temporal's history and run workspaces, checks the
commit out and runs `deploy/vm/boot.sh`, which starts everything. Both paths
report `ok <commit>` or `failed <commit>` in a guest attribute, and the
workflow waits for it. `make deploy` runs the workflow by hand, and
`make deploy-status` reads the attribute. The VM has no public address; an
operator reaches it through IAP-tunneled SSH, only to read logs.

What this shape lacks against the design above: the Temporal dev server and
the Postgres container are single copies on one disk; the sandbox host is the
control-plane host; the VM's size bounds concurrent runs; and a deploy that
replaces or restarts the VM (a change to its definition or machine type)
takes the control plane down while the new VM boots. It drains first: over
IAP SSH, `deploy/vm/drain.sh on` sets the scheduler draining, so no new task
starts, and the deploy waits until no task is `running`, up to the longest
harness profile's `max_minutes` plus ten. Past that it applies anyway and
names the tasks it interrupted in the run's summary. The new VM's boot ends
the drain. After a
rollback, the VM's metadata still names the failed commit, so a reboot before
the fix deploys boots that commit, with no rollback. Cloud SQL and Temporal Cloud
are their own pull request after the point of entry, as Terraform resources
replacing the two containers.

### Compute

- One private, regional GKE Autopilot cluster.
- One Deployment per meta-harness component: the
  meta-harness task service, the planner, the meta-harness scheduler, the meta-harness run
  service Temporal workers, the pool manager, the event service, the metering
  service, the harness analyzer, the operator console and the
  ai-api-unified-http HTTP Gateway, each with a horizontal pod autoscaler. The
  meta-harness scheduler and the pool manager each run two replicas with
  leader election.
- One per-run sandbox pod per run, with the gVisor
  runtime class (GKE Sandbox). The pool manager creates and deletes these
  per-run sandbox pods through the Kubernetes API, and the pool manager's warm
  pool is pre-created, unleased per-run sandbox pods. A default-deny
  NetworkPolicy on the `sandbox` namespace allows ingress only from the
  meta-harness run service and the pool manager on the harness server Run API
  port. It allows egress only to the HTTP Gateway's internal load balancer and
  the Secure Web Proxy.
- The meta-harness run service and the
  pool manager reach a per-run sandbox's harness server at the pod address in
  the pool manager's sandbox lease record, over mutual TLS. No load balancer
  sits in front of the per-run sandboxes, because each run talks to exactly
  one per-run sandbox pod.
- A GPU node pool runs vLLM behind the HTTP Gateway,
  as an `openai-compatible` ai-api-unified completions engine in the
  ai-api-unified `models.json` configuration.

### Managed services

| Service | Owner and use | Fails how |
|---|---|---|
| Cloud SQL for PostgreSQL | One regional high-availability Cloud SQL instance on a private IP. One schema and database role per owning meta-harness component, with IAM database authentication per component. The Cloud SQL instance has the capacity for the meta-harness event service, the heaviest writer. | Failover to the Cloud SQL standby takes about a minute. Meta-harness services retry, and event service producer clients buffer. Per-run sandboxes never touch Cloud SQL, so runs in progress keep working. New task admissions by the meta-harness scheduler and new sandbox leases from the pool manager wait. |
| Temporal Cloud | The meta-harness run service's Temporal namespace, reached over Private Service Connect. RunTask workflow history, timers and signals. | RunTask workflows stop advancing, but per-run sandboxes keep running and finish their work. When Temporal Cloud returns, meta-harness run service activities retry and re-attach to the harness server by `run_id` and `Last-Event-ID`. A run whose pool manager sandbox lease TTL expires during the outage is lost, and the meta-harness run service retries it as a new attempt. |
| Memorystore for Redis | Standard tier with a replica. One key prefix per owning component. The HTTP Gateway keeps HTTP Gateway run grants and HTTP Gateway per-run spend counters. The meta-harness scheduler keeps repo-area locks. The meta-harness run service keeps code-host API rate-limit token buckets. The event service keeps a commit notification fallback, unused while Postgres `NOTIFY` suffices. | Memorystore failover keeps the data. If Memorystore loses the HTTP Gateway's spend counters or run grants, the HTTP Gateway fails closed. The HTTP Gateway then rejects model turns for any run it cannot verify or meter. Those runs end, and the meta-harness run service retries them as new attempts. If Memorystore loses the meta-harness scheduler's repo-area locks, the meta-harness scheduler stops admitting tasks until it rebuilds the locks from the meta-harness run service's active runs. |
| Cloud Storage | One bucket per owning component. The pool manager's Cloud Storage artifact bucket holds pi session directories under `harness_session_id` and per-run sandbox logs. The meta-harness run service's bucket holds run artifacts, and the event service's bucket holds event exports. | The pool manager's sandbox drain step retries artifact collection. A per-run sandbox stays in `draining` until the pool manager saves the per-run sandbox's artifacts to the pool manager's bucket or a limit passes. Follow-up tasks that need a saved pi session wait. |
| Artifact Registry | Every container image: the per-run sandbox harness image and one image per meta-harness control-plane component. | Pods cannot pull uncached images. Running meta-harness services and warm per-run sandboxes keep running, and image rollover pauses. |
| Secret Manager | Secret Manager mounts each secret only into its owning component's pods. Model provider API keys go to the HTTP Gateway, except the ai-api-unified router classifier's provider API key. The classifier's provider API key goes to the meta-harness run service and the planner. The meta-harness run service also receives an HTTP Gateway API key. The meta-harness run service run activity uses that HTTP Gateway API key to request and revoke HTTP Gateway run grants. | Secrets load into pods at pod start, so a Secret Manager outage blocks new pods only. |
| Cloud Logging log sink and Pub/Sub | The log sink publishes the HTTP Gateway's meta-harness request records to one Pub/Sub topic, and a push subscription delivers them to the event service. | Pub/Sub keeps an undelivered message for 7 days and retries the push, so an event service outage delays `gateway_request` events and loses none. |
| Cloud NAT and Secure Web Proxy | All egress from the GKE cluster. The Secure Web Proxy enforces the per-run sandbox egress allowlist. | Fails closed: per-run sandbox egress stops, runs fail on code-host clone or push, and the meta-harness run service retries them. No bypass route exists. |

### Network

- The GKE cluster is private in its own VPC, with Cloud NAT for
  egress.
- The Secure Web Proxy allows the HTTP Gateway's
  internal load balancer address, the code host and package registries, and
  nothing else. The `sandbox` namespace's default-deny NetworkPolicy enforces
  the same limit inside the GKE cluster.
- An internal HTTPS load balancer fronts the
  ai-api-unified-http HTTP Gateway.
- An external HTTPS load balancer with Identity-Aware Proxy fronts the
  operator console and the REST API. Its URL map sends the webhook paths to a
  backend without IAP, and Cloud Armor rate-limits each client.
- Each meta-harness component has one Kubernetes
  service account bound to one Google service account. IAM grants follow
  component ownership: a component's Google service account reaches only its
  own Cloud SQL database role, Cloud Storage buckets and Secret Manager
  secrets. Only the pool manager's service account can create or delete pods
  in the `sandbox` namespace. Per-run sandbox pods run under a service account
  with no Google Cloud permissions.
- Memorystore has no per-prefix IAM, so each owning
  component's Redis client wrapper enforces that component's key prefix.
- A run grant key is an application credential of the HTTP Gateway, with no
  Google Cloud permissions. The HTTP Gateway API key that the meta-harness run service run
  activity uses to request and revoke run grants reaches only the meta-harness
  run service pods, through Secret Manager.

### Observability

An OpenTelemetry collector DaemonSet sends traces to Cloud Trace and logs to
Cloud Logging. Cloud Monitoring holds the metrics that drive the horizontal pod
autoscalers and the alerts.

### Local development

Docker Compose runs Postgres, Redis, the Temporal dev server and every
meta-harness service. Per-run sandboxes use the Docker executor backend behind
the same pool manager executor interface, from the same images, and a local
egress proxy enforces the same per-run sandbox egress allowlist. Each local
per-run sandbox is on its own internal Docker network, which has no route
out, and Docker attaches the local egress proxy to each one. Docker publishes no
port from an internal network, so the meta-harness run service reaches each harness server
through the local egress proxy's control listener.

## Build plan

| Step | State | Where |
|---|---|---|
| pi model client extension: pi's model calls through the HTTP Gateway | built | `images/harness-pi/extension` |
| Harness server and the harness server Run API | built | `images/harness-pi/server` |
| Each run as a meta-harness run service Temporal workflow (the RunTask workflow), with re-attach to the harness server on retry and cancel | built | `samples/temporal-run-task` |
| HTTP Gateway run grants: model pin and spend limit per run | built | ai-api-unified-http 1.18.0, `samples/temporal-run-task` |
| Per-run sandbox: the harness server in a Docker container per run, which the meta-harness run service run activity starts and removes with the Docker executor backend | built for local development | `images/harness-pi/Dockerfile`, `samples/temporal-run-task` |
| Per-run sandbox egress allowlist: each per-run sandbox on its own internal Docker network, with a local egress proxy that lets it reach only the HTTP Gateway, the code host and package registries | built for local development | `samples/temporal-run-task/egress-proxy`, `make egress-check` |
| A zero-cost end-to-end check: the RunTask workflow against a fake HTTP Gateway that makes no model calls, on every pull request | built | `samples/temporal-run-task/e2e.sh`, `make e2e`, `.github/workflows/e2e.yml` |
| The sandbox runs this repo's checks (#203): on every pull request, `make image-checks` has pi's bash tool run the checks a task on this repo runs (`uv sync`, ruff, the whole Python suite, and the console's install, typecheck and tests) in the built per-run sandbox image, on a clone of the checkout under test, behind the egress proxy, with the run's database beside it, against the fake HTTP Gateway. It fails when the image or the sandbox cannot run them, and the deploy waits for it. On its first runs it found the sandbox started under the wrong name once it had a database, a database host name too long for a long task ref, and every workflow test skipped in a sandbox because `temporal.download`, where Temporal's test server comes from, was not on the egress allowlist | built | `samples/temporal-run-task/stack.sh` (`checks`), `src/meta_harness/run_service/fake_gateway.py` |
| Provider-neutral tool results: one tool-result shape the HTTP Gateway converts for each model provider | built | ai-api-unified-http 1.19.0, `images/harness-pi/extension` |
| The RunTask workflow's run loop over the ai-api-unified router's candidates, with a meta-harness run service `route` activity, escalation, pins and a task-wide spend limit | built | ai-api-unified 3.30.0 (`AiRouteClassifier`), `samples/temporal-run-task`, `make fallback-demo` |
| Repo work in a run: each run starts from a fresh clone of the task's repo, pi's `bash` tool runs the acceptance checks, and the meta-harness run service's Publish step commits pi's changes, pushes the branch and opens the pull request with `Closes <issue>`. The per-run sandbox holds no code-host credential | built for local development | `samples/temporal-run-task`, `make repo-demo`, `make run TICKET=owner/name#N` |
| `submit_review`, the review run's owned pi tool | built | `images/harness-pi/extension` |
| The clone's git directory outside the per-run sandbox mount, read-only inside it, so nothing pi writes runs on the host at Publish | built | `src/meta_harness/run_service/code_host.py`, `sandbox.py`, `tests/run_service` |
| Runs sized for real work: Python 3.12, uv and Poetry in the sandbox image; profile limits, resources and bash allowlist; the run activity's timeout from the profile; tier models and thinking levels | built | `images/harness-pi`, `config/`, `tests/run_service/test_workflow.py` |
| The operator console on Mantine: a sidebar shell, data tables, a Goals page, and a Configuration view of router tiers, harness profiles and the repo registry, with versions, checked saves and rollback | built | `console/`, `src/meta_harness/config`, `tests/config` |

### Bootstrap pull requests

The bootstrap ends at the point of entry: an operator signs in to the console
web app in the cloud, creates a goal, and the meta-harness turns the goal's
labeled issues into pull requests, with no process running on the operator's
machine. Nothing self-improving starts before that. The meta-harness pull
requests merge one at a time, in this order. Each check joins the zero-cost
end-to-end check, so it runs on every later pull request without model calls.

1. `feat/control-plane-skeleton`, `feat/event-service`, `feat/task-service`,
   `feat/run-service` (merged). Then `make e2e` submits a task through the
   Task API, and the task ends with a pull request on a test repo and every
   event in the event service.
2. `feat/github-app` (merged). Then a run clones a real repo that the GitHub
   App alone can reach, and opens its pull request as the app.
3. `feat/planner-goals`, `feat/admission`. Then a labeled issue in a test repo
   becomes a task and runs to a pull request, with no command naming the task.
4. `feat/console-web-app-goals`, `feat/console-web-app-runs`. Then an operator
   creates a goal in the browser and watches its runs live.
5. `feat/deploy-cloud` (merged). Then the cloud deployment exists: the edge
   behind IAP and the VM with every service.
6. `feat/real-runs` and `docs/agents-md` in ai-api-unified-http and
   meta-harness. Then a run on any of the three repos installs it, runs its
   tests and works within limits sized for the task, and each repo tells the
   run its own rules. After the deploy check, the point of entry works: a goal
   created in the cloud console runs its issues to pull requests.

With `auto_merge` off, each pull request waits for an operator to merge it,
so the point of entry needs no review loop. After it, `feat/review-loop`
(#85) and `feat/merge` (#86) merged, built by the meta-harness. Still to
come: `feat/code-host-webhooks`, `feat/takeover`, `feat/planner-sync` and
`feat/console-web-app-inputs`.

| Pull request | Builds | Check |
|---|---|---|
| `feat/control-plane-skeleton` | `src/meta_harness/`, the `mh` command line, Docker Compose with Postgres and the Temporal dev server, migrations per owning component | `make up && make migrate` on a clean checkout |
| `feat/event-service` | The `event` table, the schema registry, `append`, `read` and `tail`, and the client library with its outbox | `mh events tail` keeps printing in position order across an event service restart |
| `feat/task-service` | The `task` table and the Task API: create, read, look up by `external_ref`, cancel | Two `POST /v1/tasks` with one `external_ref` return one task |
| `feat/run-service` | The RunTask workflow, moved from `samples/temporal-run-task`, started from a task id and writing its rows and events | `make e2e` through the Task API |
| `feat/github-app` | A read-only installation token per run for the clone, and the app token for every code-host write | `make run REPO=owner/name` on a repo the GitHub App alone can reach |
| `feat/planner-goals` | The `goal` table, the Goals API, the `fix` modality, the GitHub adapter's `list_open`, `read` and `sweep`, and issues to tasks with `depends_on` | A labeled test issue becomes a task within one sweep |
| `feat/admission` | A loop that starts queued tasks' RunTask workflows: global concurrency, repo-area locks as expiring rows until the meta-harness scheduler moves them to Redis, closed dependencies, the goal's budget | `make e2e-admission`: two tasks on one repo area run in turn |
| `feat/console-web-app-goals` | The first console web app: goal creation, the Overview and the Goal page, calling only the REST API, with an identity outside the access list refused (merged as server-rendered pages, then replaced by the React app) | An operator creates a goal in the browser, and an account outside the access list gets 403 |
| `feat/console-web-app-runs` | The console web app in React and TypeScript (`console/`), and the local edge that serves it and routes `/v1` as the signed-in operator. Lists read ahead as you scroll; the Tasks page shows running tasks as a list or as terminal tiles 4, 16 or 32 up, where only the tiles on screen stream, over one server-sent-event stream; the Task page shows the live run and loads older events as you scroll up; cancel and the kill switch, which the meta-harness scheduler carries out | The tiles of running tasks stream their events as they happen, and scrolling moves the stream to the tiles in view |
| `feat/deploy-cloud` | One VM running every control-plane service, Postgres, the Temporal dev server, Redis, the HTTP Gateway on its own configuration, the worker and its per-run sandboxes, as systemd units booted from Secret Manager; the edge on Cloud Run behind Identity-Aware Proxy with the IAP header check; the clone's git directory outside the sandbox mount (see Deployment without GKE) | `deploy/gcp.sh infra && deploy/gcp.sh edge`, then a goal created in the cloud console runs a labeled issue to a pull request, and a request without IAP credentials gets 401 |
| `feat/real-runs` | Python 3.12, uv and Poetry in the per-run sandbox image; harness profiles sized for these repos, with resources and a bash allowlist; the run activity's timeout from the profile; router tiers on Sonnet 5 and Opus 5.5, with each tier's thinking level sent on every turn; a $10.00 default task limit; an e2-standard-8 VM | `uv run pytest -q`, `npm test` in `images/harness-pi`, and `make e2e` in CI |
| `feat/terraform-deploy` | The cloud in Terraform: a platform stack and a release stack, which CI applies on every merge to main through workload identity federation; the VM replaced per commit with its state on a data disk; `make platform`, `make deploy`, `make deploy-status` | A merge to main deploys, and `make deploy-status` names the merged commit as what the VM booted |
| `feat/deploy-managed` | Cloud SQL for the control plane's tables, and Temporal Cloud for the run service namespace | `make deploy`, then the same goal check |
| `feat/code-host-webhooks` | The webhook endpoint: signature check, and check, merge and close deliveries as RunTask workflow signals | `make webhook-replay` |
| `feat/review-loop` | The review run with `submit_review`, the posted review, the fix run, and the round and budget limits (merged, #85) | `make e2e-review`: `changes`, a fix, then `clean` |
| `feat/merge` | The merge at the reviewed head SHA, `auto_merge`, the `ready` and `review_unresolved` ends, and merge conflicts as fix runs (merged, #86) | `make e2e-review` ends merged |
| `feat/takeover` | Take over, hand back and close, as audit events | `mh task take-over`, then `mh task hand-back` |
| `feat/planner-sync` | Issue webhooks, state labels and comments, blocked tasks, refused issues, the goal's status | A test issue gets `meta-harness:running`, then `meta-harness:pr-open` |
| `feat/console-web-app-inputs` | The Inputs page: queued issues by repo and by goal, with their states | A test issue moves from queued to merged in Inputs |
| `docs/agents-md` in ai-api-unified-http and meta-harness | `AGENTS.md` with the repo's test, lint and versioning rules | None |

An operator sets up what the meta-harness cannot:

- a Google Cloud project with billing, and, with `feat/deploy-managed`, a
  Temporal Cloud account;
- the GitHub App, installed on ai-api-unified, ai-api-unified-http and
  meta-harness, with its private key in Secret Manager;
- branch protection with required checks on the three repos;
- a spend limit on each model provider key the HTTP Gateway holds.

### Built by the meta-harness

Since `feat/deploy-cloud` merged, the rest of this design is filed as GitHub
issues, and `fix` goals turn them into pull requests. A goal takes one
label, so each issue carries exactly one theme label, and a theme's issues
make sense as one goal:

| Label | Theme |
|---|---|
| `meta-harness:parallel` | A repo's tasks run side by side: the parallel limit, the merge queue, `Paths:` in issues, and code laid out so tasks edit different files |
| `meta-harness:operations` | Running the deployment: deploys, workflow changes under running tasks, recovery. A failed deploy files its issue here |
| `meta-harness:console` | The console's admin and user menus: roles, the configuration editors, repositories, logs, user settings |
| `meta-harness:goal-lifecycle` | A goal from saved to met: save and start, picking issues, each task's pull request, the goal's board, completing and reopening |
| `meta-harness:goal-planning` | A `feature` goal plans its own issues: the planning profile, the plan, and its approval |
| `meta-harness:console-ux` | The console as the place to follow and steer a task: the run timeline, diffs, reviews, steering, attempts, cost, the inbox |

An issue that two goals' labels match joins the first goal that sweeps it
and stays there, which is why an issue has one theme label. A `Depends on:`
line may name an issue under another label; the task then waits `blocked`
until that issue closes, so a goal on its label never finishes before the
other theme's goal has run. Such lines are kept few, and the themes run in
the order above. `meta-harness:merge` holds the closed issues of the review
loop and the merge, and `meta-harness:planner-check` and
`meta-harness:deploy-check` hold the issues the checks run.

Each issue states its `## Acceptance checks`, the commands its run and its
review run, and names the design sections its change makes untrue, which its
pull request updates.

Design work no issue covers yet:

| Issue | Repo |
|---|---|
| The `new-system` modality with its phases, goal success criteria as commands and checks, the modality classifier and latitude. The `feature` modality's planning task and plan approval are built (#72, #73); the console's plan review is built (#75) | meta-harness |
| The `modalities` and `access` configuration kinds, beside the built tiers, profiles, repo registry and task policy | meta-harness |
| ai-api-unified decisions in the operator console: the Cloud Logging log sink, the Pub/Sub topic and push subscription, and `gateway_request` events | meta-harness |
| The metering service and the Spend view | meta-harness |
| The harness analyzer, its proposals, and the Routing view | meta-harness |
| Operator review and takeover | meta-harness |
| Code-host and tracker webhooks, and the planner's write-back to issues: state labels, comments, `meta-harness:failed`, operator issues | meta-harness |
| The pool manager, GKE, and the meta-harness scheduler as its own service | meta-harness |
| The owned `run_tests` tool, plan-first and `test_before_pr`, the pi compaction handler with a `trivial` tier, and `guardrail_blocked` events | meta-harness |
| `route` in the conversation request body, so on a run grant that pins a model one call can run on a tier range capped at the pinned model's tier | ai-api-unified-http |
| Streaming: a `send_conversation` form that yields typed events and commits to one model at its first delta, and a server-sent-events form of the HTTP Gateway conversation endpoint | ai-api-unified, ai-api-unified-http |

Once the planning task exists, the meta-harness drafts its own next issues,
and later goals use the `feature` and `new-system` modalities.
