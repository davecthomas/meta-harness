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

| Component | What it does | Code |
|---|---|---|
| **Meta-harness control plane** | | `src/meta_harness/` |
| Task service | Accepts and tracks tasks, serves the Task API, and receives code-host webhooks. | `task_service/` |
| Planner | Turns a goal into issues and issues into tasks, runs the goal loop, and syncs issues both ways with the issue trackers through one adapter interface: GitHub Issues first, then Jira and Linear. | `planner/` |
| Meta-harness scheduler | Picks the next task to run, by goal priority, modality, quotas, spend caps, repo-area locks and dependencies. | `scheduler/` |
| Meta-harness run service | Runs each task as a Temporal workflow, the RunTask workflow: routes it, runs its candidates, reviews and merges its pull request. The only caller of the ai-api-unified router. | `run_service/` |
| Pool manager | Leases per-run sandboxes by run id, keeps a warm pool, and replaces unhealthy sandboxes. | `pool/` |
| Event service | Holds the ordered record of every meta-harness domain event. | `events/` |
| Metering service | Computes cost, time and success per attempt and per task. | `metering/` |
| Harness analyzer | Proposes changes to router tiers, harness profiles and task policy from metering evidence. | `analyzer/` |
| Operator console | The cloud web app operators steer the meta-harness from. | `console/` |
| **ai-api-unified library**, in the meta-harness run service process | | the `ai-api-unified` repo |
| ai-api-unified router | Classifies a task, picks a tier, and returns the tier's ordered harness-profile-and-model candidates. | `AiRouterCompletions` |
| **Per-run sandbox**, one container per run | | `images/harness-pi/` |
| Harness server | Serves the harness server Run API and runs pi through pi's SDK. | `server/` |
| pi | The coding agent, third-party (pi.dev), used unmodified. | `@earendil-works/pi-coding-agent` |
| pi model client extension | Sends every pi model call to the HTTP Gateway. | `extension/` |
| **HTTP Gateway** | The only model endpoint a per-run sandbox can reach. Holds the provider API keys, pins each run's model, runs guardrail checks and enforces each run's spend limit. | the `ai-api-unified-http` repo |
| **Outside the system** | | |
| Code host | Repositories, pull requests, checks, reviews and webhooks. | |
| Model providers | Cloud model APIs, or local models on vLLM. | |
| Temporal Cloud, Google Cloud | Workflow engine and infrastructure. | |

The guardrails library runs inside its callers. The meta-harness run service
and the pool manager use it to scope repo access and credentials per run and
to enforce spend caps.

## Principles

- `models.json` maps each ai-api-unified router tier to its
  harness-profile-and-model candidates, and `harness_profiles.json` defines
  each harness profile's image, prompt set, pi tools and loop policy. Code
  reads both, and a new harness profile is a configuration change the harness
  analyzer measures like any other candidate.
- The meta-harness run service calls the ai-api-unified router in-process to
  route each task, and the planner calls the ai-api-unified classifier to
  classify each goal. pi's model calls reach the HTTP Gateway through the pi
  model client extension on the run's grant key, and the HTTP Gateway holds
  the provider API keys.
- The meta-harness run service starts pi, follows up, reads its events,
  cancels it and reads its usage through the harness server Run API.
- At startup each service checks every harness profile and router tier
  candidate against the harness profile images and the ai-api-unified
  configuration, and stops with the setting's name when a model, profile or
  image is missing.
- The metering service charges every run, failed runs included, to its task's
  cost and time.
- A run lasts minutes to hours. When a meta-harness process dies, the RunTask
  workflow resumes each run from its last recorded step.
- Each datastore has one owner component, and other components reach its data
  through that owner's interface. ai-api-unified owns no store.
- A per-run sandbox can read its task's repo and write nothing on the code
  host. After a run, the meta-harness run service pushes the branch, opens the
  pull request, posts reviews and creates the planning task's issues from what
  the run produced, so a prompt injection inside a run cannot reach the code
  host.

## Architecture

Arrows are calls on the target's interface. Only a datastore's owner reads or
writes it.

```mermaid
flowchart TB
    OP[Operators]
    CH[Code host and issue trackers]
    PROV[Model providers<br/>cloud or local]

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

        PLDB[(goal, plan, issue_link)]
        TSDB[(task)]
        SCDB[(repo-area locks)]
        RSDB[(run, routing_decision,<br/>config_version)]
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
    CON -->|tasks, runs, cancel, kill switch| TS & RS
    CON -->|spend| MET
    CH -->|issue, PR, check webhooks| PL & TS
    PL -->|issues: read, create, update, close| CH
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
    PL & TS & SCHED & RS & PM & HA -->|append events| EV
    MET & HA -->|tail, read events| EV
    HA -->|config pull request| CH
```

| Component | Owns | When it fails |
|---|---|---|
| Planner | `goal`, `plan`, `issue_link` | Goals stop taking new issues and planning. Tasks already queued run. A missed tracker webhook is caught by the planner's periodic sweep on recovery. |
| Task service | `task` | No new tasks or status reads. Running tasks continue, and the code host retries its webhooks. |
| Meta-harness scheduler | Repo-area locks in Redis | No new admissions. Running tasks continue, and a dead leader's locks expire by TTL. |
| Meta-harness run service | `run`, `routing_decision`, `config_version`, its Temporal namespace, code-host rate-limit buckets | RunTask workflows resume from their last step. A retried run activity re-attaches to its sandbox lease, its run grant and its event stream by `run_id`. |
| Pool manager | `sandbox`, `lease`, the artifact store | New runs wait for a lease. Running sandboxes continue, and lease TTLs apply on recovery. |
| Event service | `event`, the schema registry | Producers buffer and retry, so runs continue. Consumers catch up from their cursors. |
| Metering service | Its projection tables | Projections lag. The HTTP Gateway still enforces each run's spend limit, and the scheduler treats stale spend as at cap. |
| Harness analyzer | `proposal` | No new proposals. Runs are unaffected. |
| Operator console | Nothing | Operators lose sight of the system. Runs continue. |
| HTTP Gateway | Run grants, per-run spend counters | Model turns fail and the run fails. The run loop moves to the next candidate, and when every candidate fails this way the RunTask workflow pauses the task. |
| Harness server | The run's pi session directory | The run ends `ERROR`, and the run loop tries the next candidate. |

The meta-harness run service loads `models.json`,
`harness_profiles.json` and `task_policy.json`, and the planner loads
`modalities.json` and `repos.json`. Each keeps its files' version history.
Configuration changes only through a pull request against `config/`, including
an approved harness analyzer proposal.

Each owner has its own Postgres schema and role and its own
Redis keyspace, and no role has grants on another owner's schema.

## The harness

The meta-harness harness is pi's coding agent (`@earendil-works/pi-coding-agent`),
which the harness server runs in-process through pi's SDK. The meta-harness
adds two pieces to pi:

- the **pi model client extension**, a pi extension loaded through pi's own
  extension mechanism. The pi model client extension registers the only model
  provider pi can see in the harness profile image and sends every pi turn to
  the HTTP Gateway;
- the **harness server**, which exposes the harness server Run
  API and runs pi's `AgentSession` through pi's SDK.

Any image that serves the harness server Run API and honors the run's pinned
model can be a harness profile image, so ai-api-unified never needs to know
which agent runs.

### Model client

The pi model client extension registers one complete pi model provider with
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
   HTTP Gateway run grant's model pin or model band decides. Calls the pi
   model client extension makes itself, for pi session compaction and for
   auxiliary text (commit message, PR description), carry `route: trivial`.
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
  the `prompt_cache` retention `extended` above: 1 hour on Anthropic and
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
  extension and the owned pi extension tools. Nothing loads from the cloned
  task repo's `.pi/` directory: no pi settings, pi extensions, pi skills or
  `SYSTEM.md`.
- pi SDK sessions do not load the pi CLI's
  built-in `codemode`, `tool_search` and MCP pi extensions, and the harness
  server never adds them. `codemode` can run classifier and image models with
  the pi session's credentials.
- A `ModelRuntime` with no built-in credentials, so the pi
  model client extension's pi model is the only one pi can call. The harness
  server never calls `setModel`, so pi never switches models and the HTTP
  Gateway run grant decides the real model.
- pi's SDK `tools` option comes from the harness profile.
- pi loads `AGENTS.md` and `CLAUDE.md` from the pi working
  directory unless pi context loading is off, so the harness profile's
  `context_files` setting decides. The run service dispatcher activity puts
  the task repo's `AGENTS.md` in the first pi prompt, so turning
  pi context files on sends it twice.
- `SettingsManager.inMemory(...)` holds these pi settings,
  and pi reads no settings file:

| Setting | Value | Reason |
|---|---|---|
| `compaction.reserveTokens`, `compaction.keepRecentTokens` | The harness profile's `loop.compaction` | Compaction size is a harness profile setting |
| `retry.enabled` | `false` | pi's default model-call retry (3 retries, backoff up to 60 seconds) would stack on the HTTP Gateway's ai-api-unified fallback chain. A failed pi turn fails the run, and the RunTask workflow's Decide step moves to the next tier candidate. pi's context-overflow recovery (compact, then retry) stays on. |
| `cacheWarming` | `off` | Warming calls would be billed provider prompt cache refreshes |

The HTTP Gateway run grant decides the model, so the harness server registers
no pi virtual models (`registerVirtualModel`).

### Tools

| Tool | Source | Limits |
|---|---|---|
| `read`, `ls`, `find`, `grep` | pi built-in tool | Output capped by bytes; binary files refused |
| `write`, `edit` | pi built-in tool | The pi model client extension's tool-call hook refuses protected paths from the task repo policy |
| `bash` | pi built-in tool | Timeout and output cap per harness profile. The pi model client extension's tool-call hook refuses commands outside the harness profile's allowlist. No network beyond the per-run sandbox egress allowlist |
| `run_tests` | owned pi extension tool | Runs the acceptance checks named in the task; results emitted as `tests_run` harness server Run API events |
| `submit_review` | owned pi extension tool, meta-harness review harness profiles only | Ends the review run with verdict `clean` or `changes` and its findings, as a `review_submitted` harness server Run API event. The meta-harness run service posts them as a code-host PR review |
| `create_ticket` | owned pi extension tool, planning harness profiles only | Records one ticket draft (repo, title, body, labels, `Depends on:` lines) as a `ticket_drafted` harness server Run API event. The meta-harness run service creates the issues after the run |

When a run finishes, the run service's Publish step commits pi's changes to the task's branch, pushes it and opens
one PR per task, or pushes to the PR branch in a meta-harness fix task.
Code-host branch protection blocks default branches. The PR description
holds the run's final summary, which the harness prompt asks to list the
assumptions the run made, because no person answers questions.

A harness profile's `tools` list chooses from these tools.

### Loop policy

The pi model client extension and the harness server enforce the harness
profile's `loop` settings:

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

A pi session is one conversation with pi, named by `harness_session_id`. The
meta-harness run service picks the `harness_session_id`, one per pull request,
when the task that opens the pull request starts. Every meta-harness fix task
on that pull request continues the same pi session in a follow-up run. A meta-harness review task
starts its own pi session, so the reviewing model never sees the authoring
model's conversation.

| When | Where the conversation is | Owner |
|---|---|---|
| During a run | pi's session directory in the per-run sandbox, set through pi's SDK `SessionManager`. pi creates the pi session file once the pi session conversation has its first message, then appends each entry. | The per-run sandbox (harness server) |
| Between runs | A copy of that pi session directory in the pool manager's Cloud Storage bucket, stored under `harness_session_id` when the run ends | Pool manager |
| Between runs, briefly | The finished per-run sandbox itself, kept for the pi session for up to `sticky_window` (default 10 minutes) | Pool manager |
| On each model turn | The request the pi model client extension sends to the HTTP Gateway, which forwards it and keeps no copy | Nobody |
| In ai-api-unified | Nothing. The run service `route` activity sends the ai-api-unified router only the task prompt, and meta-harness review and fix tasks do not call the ai-api-unified router | None |
| In the event service | Each turn's text and tool calls, as a meta-harness domain event record for the operator console and the metering service. A run never resumes from it. | Event service |

A follow-up runs in a per-run sandbox that holds the pi session. Before the
pool manager sandbox lease request returns, the pool manager either hands back
the per-run sandbox it kept for the pi session or copies the pi session
directory from its Cloud Storage bucket into a fresh per-run sandbox. The
harness server then switches pi to that pi session and
continues it, so the pi session conversation keeps its tool history on the
same model.

A pi session records its working directory and never changes it, and outside
pi's interactive mode pi exits with an error when that working directory is
missing. Every per-run sandbox therefore clones the task repo at the same
path, `/workspace`, so a restored pi session finds its working directory.

### Events

The harness server subscribes to the pi session with `session.subscribe()`
and maps pi SDK events (`message_update`, `tool_execution_*`, compaction,
`agent_end`, `agent_settled`) to the typed harness server Run API events, each
with run id, sequence number and timestamp: `turn_started`, `model_call`
(model, thinking, model tokens, cost, latency, as the pi model client
extension recorded them from the HTTP Gateway), `tool_call`, `tool_result`,
`file_changed`, `tests_run`, `guardrail_blocked`, `compacted`,
`turn_finished`, `heartbeat`, `run_finished`. The `agent_settled` pi SDK
event marks the end of a pi turn that will not continue on its own. The
harness server then finishes the run or, in a follow-up, waits for the next
input on the harness server Run API.

### Harness profiles

A harness profile is the meta-harness harness's configuration and the
"harness" half of an ai-api-unified router candidate.

```json
{
  "version": "2026-11-03.1",
  "profiles": {
    "pi-lean":    {"image": "harness-pi:1.0", "prompts": "lean/v3",
                   "tools": ["read", "ls", "find", "grep", "edit", "bash", "run_tests"],
                   "loop": {"max_turns": 60, "plan_first": false,
                            "compaction": {"reserve_tokens": 16384, "keep_recent_tokens": 20000}},
                   "context_files": false,
                   "max_minutes": 20, "budget_multiplier": 3,
                   "resources": {"cpu": 2, "memory_gb": 4}},
    "pi-careful": {"image": "harness-pi:1.0", "prompts": "careful/v2",
                   "tools": "*",
                   "loop": {"max_turns": 200, "plan_first": true, "test_before_pr": true,
                            "compaction": {"reserve_tokens": 32768, "keep_recent_tokens": 40000}},
                   "context_files": false,
                   "max_minutes": 120, "budget_multiplier": 6,
                   "resources": {"cpu": 4, "memory_gb": 8}},
    "pi-review":  {"image": "harness-pi:1.0", "prompts": "review/v1",
                   "tools": ["read", "ls", "find", "grep", "bash", "run_tests", "submit_review"],
                   "loop": {"max_turns": 80, "plan_first": false,
                            "compaction": {"reserve_tokens": 16384, "keep_recent_tokens": 20000}},
                   "context_files": false,
                   "max_minutes": 30, "budget_multiplier": 2,
                   "resources": {"cpu": 2, "memory_gb": 4}}
  }
}
```

- `image` names the harness profile image for the per-run sandbox; a
  different agent is a different harness profile image.
- `prompts` names a versioned harness prompt set: system prompt, task
  template, pi session compaction template, PR template.
- `loop` sets the harness loop policy: pi turn limit, whether the first pi
  turn must produce a plan, whether tests must pass before the Publish step,
  and `compaction`, which maps to pi's
  `compaction.reserveTokens` and `compaction.keepRecentTokens`.
- `context_files` decides whether pi loads `AGENTS.md` and `CLAUDE.md` from
  the task repo.
- `max_minutes` and `budget_multiplier` bound the run. The task policy's
  `review` rules bound the meta-harness review loop.
- A review harness profile such as `pi-review` has read-only tools, and the
  run service publishes nothing from a review run.
- The meta-harness run service validates every harness profile at startup:
  the harness profile image exists, the harness prompt set exists, and every
  pi tool name is known.

## Routing

Routing happens twice. Once per task, the ai-api-unified router in the
meta-harness run service picks a tier and returns its candidates, each a
harness profile and a model, and the RunTask workflow tries them in order. On
every model call inside a run, the HTTP Gateway routes the call.

In the diagram, both `standard` candidates fail on infrastructure, so the
RunTask workflow escalates to `complex`.

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
    RT-->>WF: tier standard, tier candidates pi-lean with claude-sonnet-5, then pi-careful with gpt-5.6-luna
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-lean, claude-sonnet-5)
    SB-->>WF: harness server run ended ERROR model_error (provider outage)
    Note over WF: an infrastructure failure, so the RunTask workflow tries the next tier candidate
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-careful, gpt-5.6-luna)
    Note over WF,SB: the pool manager lease fails, no per-run sandbox within ready_timeout
    Note over WF: the standard tier is exhausted and the task policy allows escalation, so the RunTask workflow moves up one tier
    WF->>RT: run service route activity: tier candidates for tier complex
    RT-->>WF: tier candidate pi-careful with claude-opus-5-5
    WF->>SB: run service run activity: pool manager lease, HTTP Gateway run grant, harness server start run (pi-careful, claude-opus-5-5)
    SB-->>WF: harness server run FINISHED, pull request opened
    Note over WF: the RunTask workflow records three runs, their costs, route_tier complex, route_method classifier
```

Inside a run, the HTTP Gateway run grant pins the candidate's model, and pi
agent turns stay on it. The pi model client extension's compaction call sets
`route: trivial`, so the HTTP Gateway runs the compaction summary on a cheap
tier.

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
    Note over GW: a route hint turns the run grant pin into a tier band with the pinned tier as ceiling
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
one prompt. The run service `route` activity sends that prompt through
`AiRouterCompletions` in classify-only mode. The ai-api-unified router in the
run service classifies it, picks a tier, and returns the tier's ordered tier
candidates. Each tier candidate names a harness profile and the model pi uses
in the per-run sandbox. The RunTask workflow runs them.

```json
"router": {
  "tiers": [
    {"name": "trivial", "description": "Config, copy, dependency bumps, one-file mechanical edits with existing tests.",
     "models": ["harness:pi-lean/openai-compatible:qwen3-coder-next", "harness:pi-lean/google-gemini:gemini-3.5-flash-lite"],
     "thinking": "off"},
    {"name": "standard", "description": "A feature or fix inside one module, with clear acceptance checks.",
     "models": ["harness:pi-lean/claude:claude-sonnet-5", "harness:pi-careful/openai:gpt-5.6-luna"],
     "thinking": "low"},
    {"name": "complex", "description": "Cross-module changes, migrations, concurrency, security-sensitive code, or vague requirements.",
     "models": ["harness:pi-careful/claude:claude-opus-5-5", "harness:pi-careful/openai:gpt-6-sol"],
     "thinking": "high"}
  ],
  "classifier": {"engine": "openai-decisions", "model": "gpt-6-luna", "min_confidence": 0.7},
  "default_tier": "standard",
  "on_exhausted": "escalate"
}
```

A tier candidate takes the form `harness:<harness profile>/<provider>:<model>`.
The ai-api-unified router reads `harness` as its provider and returns the
string unchanged. In classify-only mode (`AiRouteClassifier`) the router never
builds or probes a candidate, so the meta-harness run service validates each
candidate itself at startup: its harness profile exists and names an image
the executor backend can run, and its `<provider>:<model>` is a model the
ai-api-unified library has configured.

The ai-api-unified router's classifier model reads the latest user message,
so the meta-harness run service dispatcher activity puts the task spec in
that message and keeps instructions in the system prompt.

The run service `route` activity builds one ai-api-unified router client per
process and shares it across tasks; the library keeps per-call state in
context variables. The meta-harness's own configuration files load the same
way: validated at startup, held in memory, and reloaded on a change, with the
new version recorded in `config_version`.

- The ai-api-unified router classifies the task with its classifier model,
  and below `min_confidence` it picks `default_tier`.
- The RunTask workflow tries the tier's candidates in order. A run that ends
  on infrastructure (no per-run sandbox within `ready_timeout`, a rate limit or
  exhausted quota at the model, a lost harness server, or `model_error` from a
  provider outage) moves to the next candidate. A run that ends on a limit
  (`max_turns`, `max_minutes`, `spend_limit`) ends the task.
- When a tier runs out and `on_exhausted` is `escalate`, the RunTask workflow
  calls the `route` activity again with `route_floor` one tier up, within the
  task policy's limits. A task the classifier judged hard stays on hard tiers.
- A meta-harness fix task, an exploration run and a task with a `pin` field
  skip routing and run one named pair, such as
  `harness:pi-careful/claude:claude-opus-5-5`, with no escalation. A fix task
  pins the pair that opened its pull request, so it continues that pull
  request's pi session on the same model. A review task tries the task
  policy's reviewer pairs in order. The meta-harness run service checks a
  pinned model against the ai-api-unified allow and deny lists before any
  lease, and the RunTask workflow records `route_method: model_pin`.
- Each router call carries its own `route_floor` and `route_ceiling`, because
  one router client serves many tasks at once.
- The RunTask workflow records every run's cost, failed runs included.

Caller hints use the ai-api-unified router's per-call options:

- `complexity: high` on a task passes `route_floor: "complex"`.
- `max_tier: standard` passes `route_ceiling: "standard"`.
- A retry after a failed attempt passes `route_floor` one tier above the
  failed attempt.

The RunTask workflow learns routing only from
the run service `route` activity's return value, and cost only from the run
service run activity's Collect step, never from ai-api-unified library
events.

| Where | Fields |
|---|---|
| Run service `route` activity result | From the ai-api-unified router: `route_tier`, `route_method`, `route_confidence`, `thinking`, the ordered tier candidates, and `classifier_usd` for the ai-api-unified router classifier model call |
| Run service run activity Collect result | `run_id`, `sandbox_id`, the harness profile, `end_reason`, `turns`, `pr_url`, `elapsed_ms`, the prompt cache hit rate, the run's model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens` and `cache_write_1h_tokens`) from the harness server Run API `GET /usage`, and the dollars the HTTP Gateway counted against the run's HTTP Gateway run grant |
| `preview_route()` | Before a run: tier, confidence and `usd_max`, used for the run's HTTP Gateway run grant spend limit |

The RunTask workflow writes the run service `route` activity result as the
meta-harness domain event `task_routed`, and each run service run activity
Collect result as one `attempt_finished`. Each run's
`attempt_finished` holds its `run_id`, so it joins the pool manager's
lease record for that run.

### Task policy

`config/task_policy.json` holds the rules for task types, exploration, run
budgets and review.

```json
{
  "version": "2026-11-03.1",
  "floors_by_task_type": {"security": "complex", "dependency": "trivial"},
  "exploration": {"share": 0.05, "eligible_tiers": ["trivial", "standard"],
                  "challengers": ["harness:pi-careful/openai-compatible:qwen3-coder-next"]},
  "review": {"reviewers": ["harness:pi-review/claude:claude-opus-5-5",
                           "harness:pi-review/openai:gpt-5.6-luna"],
             "max_rounds": 3, "merge": true, "operator_alert_minutes": 240},
  "max_run_minutes": 240
}
```

- `floors_by_task_type` sets `route_floor` on the router call.
- The RunTask workflow pins a configured share of eligible
  tasks to a challenger harness-profile-and-model pair, so the harness analyzer keeps current data on every harness profile
  and model. Complex and security tasks never explore.
- A run's HTTP Gateway run grant spend limit is
  `preview_route()`'s `usd_max` for the tier times the harness profile's
  `budget_multiplier`, capped by the task's `max_usd`. A task's `max_usd`
  also caps the total of its meta-harness review and fix tasks.
- `reviewers` lists the meta-harness review task's
  harness-profile-and-model pairs in order. The RunTask workflow skips any
  reviewer pair whose model wrote the pull request, so a different model
  always reviews. `max_rounds` caps the meta-harness fix tasks per pull
  request. `operator_alert_minutes` sets when a review round blocked on an
  operator raises an alert. `merge: false` leaves a clean pull request open,
  and a repo's policy can set it per repo.

### The run loop

The RunTask workflow runs a task's tier candidates, one run each. The run
service `route` activity sends the task prompt to the ai-api-unified router in
classify-only mode and returns the tier, `route_method`, `route_confidence`
and the tier's ordered candidates. For each candidate the RunTask workflow
runs one run activity, which takes the steps Identify through Collect below.
The RunTask workflow then takes the Decide step: next candidate, escalation
or end. It checks the budget between candidates, because it holds the task's
remaining spend, its attempts so far and the task policy's limits.

| Step | What happens |
|---|---|
| Identify | `run_id` is the RunTask workflow id, the attempt number and the tier candidate index, the same on every retry of the run activity. A meta-harness review or fix task has its own RunTask workflow id, so its runs get their own `run_id`s. |
| Lease | Asks the pool manager for an existing pool manager lease on `run_id` first, so a retried run activity re-attaches to a run in progress. Otherwise requests `lease(run_id, image, harness_session_id, resources)` for the harness profile's image. The pool manager response holds the per-run sandbox address, the pool manager lease TTL and the `session_state`: `new`, `kept` or `restored`. In a deployment without the pool manager, the run activity starts one per-run sandbox per run itself, with the Docker executor backend. |
| Grant | Asks the HTTP Gateway for an HTTP Gateway run grant, which the HTTP Gateway issues and stores. The run grant holds the run id, tags, the tier candidate's inner model as a pin, the spend limit, and an expiry at `max_minutes` plus grace. Only the run grant key goes to the per-run sandbox. A retried run activity asks again with the same terms and gets a new run grant key for the live run grant. |
| Start | When `session_state` is `new`, `POST /run` on the per-run sandbox's harness server Run API with the prompt, acceptance checks, harness profile, limits and run grant key. When it is `kept` or `restored`, `POST /followup` with the new input and its own run grant key. |
| Wait | Follows the harness server's event stream until `run_finished`, heartbeating the last harness server event id on every event. The run activity's heartbeat timeout catches a dead Temporal worker, and its start-to-close timeout, the pool manager lease TTL, is the hard upper bound. A RunTask workflow cancel reaches the run activity through its heartbeat, and the run activity sends `POST /cancel` to the harness server. |
| Collect | Reads the harness server's `/usage`, and a finished run's changes from `GET /bundle`. Then revokes the HTTP Gateway run grant and releases the pool manager lease. Returns the terminal state: status, end reason, turns, the files changed, the run's final summary, elapsed time and model token totals. The terminal state also holds the dollars the HTTP Gateway counted against the run grant. The RunTask workflow records one cost line per run from that figure. |
| Decide | In the RunTask workflow. A run that ended `FINISHED` ends the candidate loop. A run that ended `ERROR` for an infrastructure reason (no per-run sandbox, a lost harness server, `model_error` from a provider outage) tries the next tier candidate. A run that ended on a limit (`max_turns`, `max_minutes`, `spend_limit`) is final. Another candidate would spend the budget again, so only the task policy's retry-up rule or an operator repeats a run that ended on a limit. |
| Publish | A run service activity, after Decide ends the loop on a finished run. Commits the run's changes to the branch `mh/<task>` and pushes it with the run service's code-host app token. Opens the pull request, labeled `ai:meta-harness`, with `Closes <ticket>` and the run's final summary. A retried Publish finds the branch and the pull request it made. A finished run that changed nothing fails the task. A fix task pushes to its pull request's branch. With the Docker executor backend, Publish commits and pushes from the workspace the host mounts. |

Each tier candidate is one run and one per-run sandbox. The RunTask
workflow's Temporal history records every attempt, tier candidate and
decision. The RunTask workflow also writes each run's `attempt_finished`
meta-harness domain event, which the operator console and the harness
analyzer read from the event service.

The RunTask workflow calls no model. The classifier call happens in the run
service `route` activity, through the ai-api-unified library.

### Model calls inside a run

The harness server's pi model client extension sends every pi model turn to
the HTTP Gateway's native conversation endpoint, with the run grant key as
its bearer credential. The HTTP Gateway:

1. Looks up the HTTP Gateway run grant by its run grant key: the run, harness
   profile, model policy and spend limit.
2. Sets the ai-api-unified observability context: `caller_id` is the
   meta-harness, tags are run, task, harness profile, harness and model.
3. Sends the turn through the ai-api-unified router inside the HTTP Gateway.
   With a **pinned** run grant model policy (the default) the HTTP Gateway
   wraps the call in the ai-api-unified library's model-pin scope for the
   tier candidate's inner model. The turn skips the classifier model call and
   runs on that model with the ai-api-unified fallback chain, cooldowns,
   guardrails and caching intact. With a **banded** run grant model policy
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
   events. The meta-harness reads no HTTP Gateway event output.

A request body with `route`, such as the `route: trivial` on compaction and
auxiliary calls, runs that one call on the named tier, capped at the pinned
model's tier. An unknown route falls back to the pinned model, and pi agent
turns, which carry no `route`, stay pinned. Under a banded policy the router
honors the hint within the band.

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
    Checks --> Reviewing: required checks passed, review task ticketed
    Checks --> Fixing: a required check failed, fix task ticketed
    Reviewing --> Fixing: verdict changes, fix task ticketed
    Reviewing --> Merging: verdict clean
    Reviewing --> Ready: verdict clean, merging off
    Fixing --> Checks: fix task pushed commits
    Merging --> Closed: PR merged
    Merging --> Fixing: merge conflict, fix task ticketed
    Checks --> Unresolved: fix tasks or task budget used up
    Reviewing --> Unresolved: fix tasks or task budget used up
    Closed --> [*]
    Ready --> [*]
    Exhausted --> [*]
    Unresolved --> [*]
```

Each task has one RunTask workflow, whose Temporal workflow id is the task
id, and every run of the task happens inside it. A review or fix task is a
task of its own, with its own RunTask workflow. `start` uses Temporal's Fail
conflict policy and Reject Duplicate reuse policy, so a task id starts at most
one RunTask workflow, ever.

The RunTask workflow runs these steps:

1. The meta-harness scheduler grants a slot and calls
   the meta-harness run service's `start`.
2. The guardrails library resolves the target repo's repo policy
   and mints a read-only code-host access token scoped to the target repo, for
   the clone. The run service dispatcher activity builds the harness prompt
   from a versioned prompt template: task spec, acceptance checks, the target
   repo's `AGENTS.md`, and the instruction to finish with a summary for the
   pull request, which the Publish step opens with the label `ai:meta-harness`.
3. The run service `route` activity routes the task with its floors,
   ceilings and exploration pin. The RunTask workflow writes the
   `routing_decision` row, appends `task_routed`, and runs the candidates
   through the run loop, appending one `attempt_finished` per run.
4. The run service run activity is the only consumer of its
   sandbox's harness server Run API event stream. It writes the `run` row,
   appends the run's harness server Run API events through the event service
   client library in batches, heartbeats the RunTask workflow, and marks the
   harness run stalled when the harness run misses a heartbeat window.
   Code-host webhooks for the run's PR (checks completed, merged, closed)
   arrive at the task service, which signals the RunTask workflow.
5. The RunTask workflow tickets review and fix tasks on the pull request
   until a review comes back clean, then merges it.
6. When the PR merges or closes, when the meta-harness review loop
   ends `ready` or `review_unresolved`, or when attempts and task budget are
   spent, the RunTask workflow appends the outcome meta-harness domain events
   and sets the task's terminal status through the task service, which
   reports it.
7. The operator console sends cancel or kill switch (per run, per
   caller, global) to the meta-harness run service, which appends the audit
   meta-harness domain event with the operator as actor. The RunTask workflow
   cancels the run service run activity, which calls the harness server Run
   API `POST /cancel` on the per-run sandbox directly, revokes the HTTP
   Gateway run grant, and releases the sandbox lease
   with the pool manager, which destroys the per-run sandbox.

Every terminal state releases the pool manager sandbox lease. The pool
manager then collects run artifacts into its artifact store and destroys the
per-run sandbox, or keeps it for the same pi session's next follow-up run.

Timeouts come from each harness profile (`max_minutes`), and the task policy
sets the hard ceiling.

### Review loop

The RunTask workflow of the task that opened a pull request owns it until it
merges or closes, and runs this loop:

1. The RunTask workflow waits for the code-host checks webhook on
   the PR's head commit. A failed required check goes to step 3 with the
   check's output. Passing checks go to step 2.
2. The RunTask workflow tickets a meta-harness review task through
   the task service, with `parent_task_id`, the PR URL and the head commit
   SHA. The review task pins the first reviewer pair in the task policy whose
   model did not write the PR, and starts a new pi session on a
   meta-harness review harness profile with no edit tools. The review run
   reads the PR diff, the parent task's spec and acceptance checks, and runs
   the tests. Its `submit_review` tool ends the run with verdict `clean` or
   `changes` and its findings. The meta-harness run service posts them as a
   pull request review and records the verdict with the reviewed head SHA in a
   `review_submitted` event.
3. On `changes` or a failed check, the RunTask workflow tickets a
   meta-harness fix task through the task service, with `parent_task_id`,
   the PR branch, and the findings or check output as the task description.
   The fix task pins the pair that opened the pull request and continues its
   pi session in a follow-up run (`POST /followup`), and its Publish step
   pushes to the pull request's branch. The loop returns to step 1.
4. On `clean`, the meta-harness run service merges the PR through
   the code-host API at the reviewed head SHA, so a commit pushed after the
   review never merges unreviewed. A merge conflict with the base branch
   tickets a fix task to rebase. With `merge: false` in the task policy or
   the repo policy, the RunTask workflow leaves the clean PR open and ends the
   task `ready`.

Each meta-harness review and fix task is a task of its own: the task service
stores it, the meta-harness scheduler admits it under the parent task's
repo-area lock, and its own RunTask workflow runs it, records its runs and
cost, and reports its result to the task service. The task service then
signals the parent task's RunTask workflow. The parent task holds its
repo-area lock until the PR merges or closes.

The loop stops at the task policy's `max_rounds` meta-harness fix tasks per PR
or when the review and fix tasks have spent the parent task's `max_usd`. The
RunTask workflow then leaves the PR open, ends the task `review_unresolved`,
and a meta-harness alert rule fires. A PR closed on the code host
ends the loop and the task.

### Operator review

When a meta-harness review run starts, the meta-harness run service appends a
`review_started` meta-harness domain event. The operator console shows it as a
notification to every operator watching, with the PR link and an offer to
review the PR too. The offer needs no answer:

- The offer closes when the agent review run ends, and the
  review round decides on the agent's verdict alone.
- An operator who takes the offer sends a review claim from the
  operator console to the meta-harness run service. The run service records
  the claim in its `operator_review` table and appends
  `operator_review_claimed` with the operator as actor. The first claim wins,
  and the operator console tells later operators who holds it. The agent
  review run still finishes, and the operator console shows its findings to
  the operator.
- A claimed review round waits for the operator. The review task's
  RunTask workflow waits on a Temporal signal, so the wait survives restarts.
  The agent review run's per-run sandbox is released when that run ends, and
  the metering service counts run time only, so the wait adds no
  dollar-hours. The
  parent task keeps its repo-area lock while it waits.
- The operator reads the PR on the code host and submits a
  verdict, `clean` or `changes`, with findings, from the operator console. The
  meta-harness run service posts the operator's findings as a code-host PR
  review that names the operator, and appends `operator_review_submitted`.
  The round's verdict is `changes` when the agent or the operator says
  `changes`, and the fix task gets both sets of findings.
- The operator can release the claim from the operator console,
  and the round then decides on the agent's verdict. A meta-harness alert
  fires when a claimed round has waited longer than the task policy's
  `review.operator_alert_minutes`. Nothing releases a claim on its own.

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
    W->>EV: append task_closed
    W->>TS: set status: closed
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
    GW->>GW: body carries route: pin becomes band,<br/>ceiling = HTTP Gateway run grant pinned model's tier
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
    participant S as Meta-harness scheduler
    participant W as Meta-harness run service: RunTask workflow of the task that opened the PR
    participant RV as Meta-harness run service: RunTask workflow and run activity of a review task
    participant FX as Meta-harness run service: RunTask workflow and run activity of a fix task
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
    W->>TS: create review task (parent_task_id, PR URL, head SHA)
    S->>RV: admit under the parent task's repo-area lock, start RunTask workflow
    Note over RV: first reviewer pair in the task policy whose model did not write the PR, new pi session
    RV->>PM: Lease step: lease(run_id, pi-review image, new harness_session_id)
    Note over RV: Grant step: HTTP Gateway run grant for the reviewer model
    RV->>SR: Start step: POST /run (PR diff, parent task spec, acceptance checks, run grant key)
    RV->>EV: append review_started (PR URL, head SHA)
    EV-->>CON: tail: review_started
    Note over CON: notification to watching operators, with an offer to review the PR too
    opt an operator claims the review before the agent review run ends
        CON->>RV: claim review (operator)
        RV->>EV: append operator_review_claimed (actor)
    end
    SR->>SR: read the diff, run the tests
    SR->>SR: submit_review: verdict and findings
    SR-->>RV: run_finished, verdict changes, findings
    RV->>CH: post the agent's findings as a PR review
    opt the review is claimed
        Note over RV: the review round blocks on a Temporal signal until the operator submits or releases
        CON->>RV: operator verdict and findings
        RV->>CH: post the operator's findings as a code-host PR review naming the operator
        RV->>EV: append operator_review_submitted (actor)
    end
    RV->>EV: append review_submitted (verdict changes if the agent or the operator says changes, both findings, head SHA)
    RV->>TS: set status: done, verdict changes
    TS->>W: signal RunTask workflow: review task done
    W->>TS: create fix task (parent_task_id, findings as untrusted data, pin: the PR's pair)
    S->>FX: admit under the parent task's repo-area lock, start RunTask workflow
    FX->>PM: Lease step: lease(run_id, the PR's image, the PR's harness_session_id)
    PM-->>FX: sandbox, session_state kept or restored
    Note over FX: Grant step: HTTP Gateway run grant pinned to the PR's model
    FX->>SF: Start step: POST /followup (findings only, run grant key)
    SF->>SF: switch_session, follow_up, fix, run the tests
    SF-->>FX: run_finished
    FX->>CH: Publish step: push the fix to the PR branch
    FX->>TS: set status: done
    TS->>W: signal RunTask workflow: fix task done
    Note over CH,W: the loop repeats from the next checks webhook, until a clean review on the PR head with passing checks
    W->>CH: merge the PR at the reviewed head SHA
    CH->>TS: code-host webhook: PR merged
    TS->>W: signal RunTask workflow: PR merged
    W->>EV: append task_closed
    W->>TS: set status: closed
```

#### An operator cancel or kill switch

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant CON as Operator console
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

    CON->>W: cancel (per run, caller, or global kill switch)
    W->>EV: append operator_action (actor, scope)
    W->>A: cancel the run activity
    A->>SB: POST /cancel (direct, per-lease harness server bearer token)
    SB->>SB: abort, clear_queue, flush pi session
    SB-->>A: status CANCELED (SSE)
    A->>GW: revoke HTTP Gateway run grant (run_id)
    A->>PM: release pool manager sandbox lease
    PM->>SB: drain: collect artifacts and /usage, then destroy
    A-->>W: terminal state canceled, partial cost
    Note over W,A: the cancel never travels through the ai-api-unified router
    W->>W: run end_reason canceled (owned)
    W->>EV: append attempt_finished with partial cost
```

## Harness execution

### Sandbox

Each run gets one per-run sandbox: a pod with the gVisor runtime class in the
`sandbox` namespace.

- The pod runs the harness profile's image, built from a common base with git,
  language toolchains, Node, pi, the pi model client extension and the harness
  server. A harness profile names another image when it needs another
  toolchain or another coding agent.
- The task's repo is cloned at `/workspace`, the same path in every sandbox,
  with the run's read-only code-host access token. The run service's Publish
  step reads the run's changes through `GET /bundle` and pushes them. With the
  Docker executor backend, the run service clones on the host into the mounted
  workspace and publishes from it.
- The HTTP Gateway URL is set in the image, and the run grant key arrives in
  the `POST /run` or `POST /followup` body.
- Egress reaches only the code host, package registries and the HTTP Gateway.
- CPU and memory come from the harness profile, 2 vCPU and 4 GB by default.
- The pool manager owns the sandbox. It serves one run at a time, and when the
  run ends the pool manager destroys it, or keeps it for the same pi session's
  next follow-up run.

### Harness server

The harness server is a small TypeScript service in every per-run sandbox. It
serves the harness server Run API to the run service run activity and runs pi
in-process through pi's SDK, using `AgentSessionRuntime` for `newSession()`,
`switchSession()` and `fork()`.

| Endpoint | Purpose | pi SDK calls |
|---|---|---|
| `GET /health` | Harness server readiness: pi model client extension registered, the workspace directory exists, HTTP Gateway reachable. A `warm` per-run sandbox passes it before it clones any task repo | pi model runtime state |
| `POST /run` | Start a new pi session for the task service task. The body holds the prompt, acceptance checks, harness profile, limits and the HTTP Gateway run grant key | `newSession()`, `prompt()` |
| `POST /followup` | Continue the pi session the per-run sandbox holds, kept or restored by the pool manager before the pool manager sandbox lease returned. The body holds the new input and the HTTP Gateway run grant key, and no pi session reference. | `switchSession()` for a restored pi session, then `prompt()` |
| `GET /events` | Harness server Run API events as server-sent events, resumable with `Last-Event-ID` | `session.subscribe()`: `message_update`, `tool_execution_*`, pi compaction events, `agent_end`, `agent_settled` |
| `GET /status` | `CREATING`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELED`, `EXPIRED`, plus the last harness server heartbeat event | pi session state |
| `POST /cancel` | Stop pi. The pi session file keeps every pi session entry written before the cancel, so a later follow-up run can continue the pi session. | `abort()`, which waits until the pi session is idle |
| `GET /usage` | Model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`), model call counts, and the HTTP Gateway's running total, as the pi model client extension recorded them. These totals serve reconciliation against the run's `model_call` meta-harness domain events | pi model client extension counters |
| `GET /metrics` | Harness server OpenTelemetry metrics: pi turns, pi tool calls, files changed, test runs, elapsed time | harness server counters |
| `GET /bundle` | The run's changes against the commit it cloned, as a git bundle, for the run service's Publish step. The Docker executor backend reads the mounted workspace instead | none: git in the workspace |

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

The backends are Kubernetes pods on GKE, Docker for development and the first
cloud deployment, managed sandbox services, and a fake that replays recorded
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
token and the sandbox clones the task repo at `/workspace`. The run grant key
arrives later, in the `POST /run` or `POST /followup` body. A `kept` sandbox
holds a finished run's repo and pi session for up to `sticky_window`, with its
run grant already revoked. A `draining` sandbox is having its pi session
directory, harness server logs and `/usage` collected. A `destroyed` sandbox
keeps its `sandbox` record for audit.

The run activity requests `lease(run_id, image, harness_session_id,
resources)`, idempotent on `run_id`. The response holds the sandbox address,
the lease TTL and a `session_state`:

- `kept`: the sandbox kept for that pi session is still within its
  `sticky_window`, and the pool manager hands it back.
- `restored`: the artifact store holds a pi session directory for that
  `harness_session_id`. The pool manager copies it into a warm sandbox of that
  image, which clones the repo at `/workspace`, the working directory the pi
  session recorded.
- `new`: the pool manager hands out a warm sandbox of that image, or
  provisions one.

A lease's TTL is the harness profile's `max_minutes` plus a grace period, and
the pool manager destroys the sandbox when it expires, whether or not the run
activity released the lease, so a crashed run activity leaks nothing. The
lease is the only way to a sandbox's address, and the harness server Run API
accepts only that lease's bearer token. When a run ends, the pool manager
saves its pi session directory to the artifact store under
`harness_session_id` before it keeps or drains the sandbox. A follow-up for
the same pi session within `sticky_window` (default 10 minutes) gets the kept
sandbox back and skips the restore. A kept sandbox stays on its lease TTL and
its one pi session.

The pool manager keeps a warm target per image (`warm: 4`), adjusted from the
scheduler's queue depth per image. A warm sandbox holds no repo and no secret.
The pool manager replaces a warm sandbox older than `warm_ttl` (default 2
hours), and per-image and global maximums cap the total. The GKE node
autoscaler follows the pool manager's demand.

A sandbox whose harness server is not healthy within `ready_timeout` is
replaced, and the lease goes to the next warm sandbox. During a run, two missed
harness server heartbeat windows mark the sandbox `unhealthy`; the pool manager
destroys it, and the RunTask workflow's Decide step decides whether another
candidate runs. The pool manager replaces an unhealthy sandbox and probes idle
warm sandboxes on an interval. Every 30 seconds it reconciles: it destroys
sandboxes with no record or an expired lease, marks records whose pod has
vanished, and provisions back to each image's warm target.

A sandbox serves one pi session: one run, and the follow-ups `sticky_window`
hands back to it. A fresh sandbox per pi session is the security boundary
between pi sessions, so recycling is drain, destroy and provision a
replacement. When a harness profile changes its image version, the pool
manager stops leasing the old image, replaces its warm sandboxes, and lets
leased sandboxes on it finish. On a node drain, warm sandboxes go at once and
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

A task's attempts include its review and fix tasks, which the metering service
rolls up to the parent by `parent_task_id`, and it reports the review loop's
share apart. Each run's `attempt_finished` event, with the dollars the HTTP
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
later turn, and it raises an alert.

### Spend controls

- The HTTP Gateway refuses a turn once the run has spent its run grant's
  limit, and the harness server then ends the run `ERROR` with `spend_limit`.
- The harness server ends a run `EXPIRED` at `max_minutes`, and the lease TTL
  destroys the sandbox if the harness server does not.
- The scheduler stops admitting a caller's tasks at the caller's daily spend
  cap, and warns at 80%.
- A hard daily global spend cap and a kill switch stop everything.

## Scheduling

### Admission

The meta-harness scheduler runs an admission pass every few seconds and on
every task event:

1. It orders tasks by priority, then age.
2. It admits a task when global run concurrency is under its limit, the caller
   is under its concurrency quota and daily spend cap, the pool manager has
   sandbox capacity, and no other run holds the task's repo area.
3. A repo-area lock covers the repo plus the top-level directories the task
   names, or the whole repo for a task that names none.
4. It admits a goal's task once every issue the task depends on is closed,
   while the goal is active, has budget left, and is within its modality's
   parallelism.

### Throughput

| Resource | Scales by | Notes |
|---|---|---|
| Per-run sandboxes | Warm targets and maximums per image | The GKE node autoscaler follows the pool manager's demand; a managed sandbox service can add a second pool |
| HTTP Gateway | Replicas | All model traffic goes through it, so it has its own SLO and canary deploy |
| Model providers | ai-api-unified fallback chains per model | Fallback and cooldowns spread load across providers |
| Control plane | Stateless replicas and Temporal workers | One run activity per active run |
| Code host API | The GitHub App's installation and webhooks | Conditional requests, no polling |

Per run, the control plane's own load is one router classification, about two
short completions, and the run's event stream. pi's model turns go straight
from the sandbox to the HTTP Gateway.

## Task API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/tasks` | Submit a task, idempotent on `external_ref`. Returns the task id |
| GET | `/v1/tasks?external_ref={ref}` | The caller's task with that `external_ref` |
| GET | `/v1/tasks/{id}` | State, current run, routing decision, pull request URL and outcome |
| GET | `/v1/tasks/{id}/events` | The task's run events as server-sent events |
| POST | `/v1/tasks/{id}/cancel` | Cancel the active run and close the task |
| GET | `/v1/runs/{id}` | Run state, candidate, token usage and cost so far |
| POST | `/v1/webhooks/code-host` | Pull request, check and review webhooks for pull requests the meta-harness opened |
| GET | `/v1/profiles` | The active harness profiles, tier candidates and task policy, with their versions |
| GET | `/health` | Liveness and configuration identity |

| Task field | Required | Meaning |
|---|---|---|
| `external_ref` | yes | The caller's own id, unique per caller |
| `repo` | yes | The repository URL |
| `task_type` | yes | `bug`, `feature`, `refactor`, `dependency`, `test`, `security`, `docs`, or `plan` for a planning task. The meta-harness alone creates `review` and `fix` tasks |
| `parent_task_id` | no | On a review or fix task: the task whose pull request it reviews or fixes |
| `priority` | yes | Higher runs first |
| `title`, `description` | yes | The task text, treated as untrusted |
| `acceptance_checks` | yes | Commands or check names that define success |
| `paths` | no | The paths the task touches, which scope its repo-area lock |
| `complexity` | no | `low`, `medium` or `high`, which becomes `route_floor` |
| `max_tier` | no | A tier name, which becomes `route_ceiling` |
| `max_usd` | no | The task's spend cap, which caps each run's grant |
| `pin` | no | One candidate, such as `harness:pi-careful/claude:claude-opus-5-5`, run with no routing or escalation |
| `goal_id` | no | The task's goal. The planner sets it |
| `depends_on` | no | The `external_ref`s this task waits for |

Each caller authenticates with its own Task API key, which sets its
concurrency quota and daily spend cap. A unique index on `(caller,
external_ref)` deduplicates submissions and serves the `external_ref` lookup.

### Goals

A goal holds the work toward one outcome: its tasks come from GitHub issues
across one or more registered repos. Goals run side by side and compete for run
capacity by priority.

| Field | Meaning |
|---|---|
| `statement` | What the goal is, in prose. The planning task reads it |
| `repos` | The registered repos the goal may change |
| `label` | The GitHub issue label that queues an issue for this goal, in any of its repos |
| `modality` | `fix`, `feature` or `new-system`, which picks the goal's rules. The planner classifies it when it is unset |
| `latitude` | 0 to 1: how far the planner may go beyond what the goal names. The modality's default when unset |
| `success_criteria` | Checks the planner evaluates to decide the goal is met: commands against a repo, an issue query such as "no open issues with the label", or a check name |
| `max_usd`, `priority` | One budget and one priority for all the goal's tasks, review and fix tasks included |
| `status` | `planning`, `active`, `paused`, `met`, `stalled`, `budget_exhausted` or `canceled` |

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/goals` | Create a goal from `statement`, `repos` and `label`; the planner sets the rest. A `feature` or `new-system` goal starts in `planning` |
| GET | `/v1/goals/{id}` | The goal, its issues with their states and dependencies, its plan or phases, its criteria and its spend |
| POST | `/v1/goals/{id}/approve` | Approve the pending plan or phase |
| POST | `/v1/goals/{id}/pause`, `/resume`, `/cancel` | Steer the goal |

For every active goal the planner runs a loop:

1. Each open issue with the goal's label in one of its repos becomes a task,
   with `external_ref` `github:<owner>/<repo>#<number>`. An issue in another
   repo is refused, with a comment saying why.
2. The RunTask workflow routes and runs each task within the modality's
   routing floor and ceiling.
3. The review loop reviews each pull request to the modality's bar, merges it
   and so closes its issue.
4. When the goal's issues are closed, or a phase ends, the planner evaluates
   the success criteria.
5. If they pass, the goal is `met`. If work is still queued, the loop goes on.
   If nothing is queued, a `fix` goal waits for issues, and a `feature` or
   `new-system` goal gets a planning task for its next step.

A goal needs only its statement, repos and label. The planner classifies the
statement's modality with the ai-api-unified classifier's `classify_choice`,
the call the router uses to pick a tier, reading each modality's
`description`. At or above `classifier.min_confidence` it sets the modality;
below it, or on a refusal, it sets `feature` and lists the goal as "modality
uncertain" in the console, where an operator confirms or changes it without
holding the goal up. The classifier also sets an unset latitude, starting from
the modality's default and moving it up for a statement that asks for
open-ended improvement and down for one that names an exact change. Each
classification is recorded as `goal_classified` with its confidence, so the
harness analyzer can measure how often operators override it. An operator can
change the modality or the latitude at any time; a new modality applies from
the next planning task.

#### Modalities

`config/modalities.json` defines the three modalities.

| | `fix` | `feature` | `new-system` |
|---|---|---|---|
| Work comes from | A standing queue: issues with the label, taken as they arrive | One spec in the goal statement, which a planning task breaks into dependency-ordered issues across the goal's repos | The goal statement. The planning task writes one phase of issues at a time, and replans after each phase from the repos as they are |
| First work | The issue itself | The plan | A design document and a skeleton that runs end to end, which every later issue must keep working |
| Parallelism | High: independent issues, one per repo area | The dependency graph | Low while the skeleton is built, higher once it exists |
| Routing | `route_ceiling: standard`; escalation on failure | Floor `standard` | Floor `complex` for the design and skeleton issues, `standard` after |
| Bar to merge | A regression test that fails before and passes after, a small diff, no public API change | Each issue's acceptance checks, and a goal-level acceptance check once the last issue merges. Docs and a version bump per the repo's rules | Conformance to the goal's design document, the end-to-end check green, and each phase ending runnable |
| Operator checkpoints | None | Approve the plan before the first task runs | Approve each phase's plan |
| Measured by | Queue size, time to merge, cost per merged fix | Criteria met, spend against budget | Phase criteria met, then goal criteria met |

```json
{
  "version": "2026-11-07.1",
  "classifier": {"min_confidence": 0.7},
  "modalities": {
    "fix":        {"description": "Fix defects that already have an issue each. Small, bounded changes to code that exists.",
                   "intake": "queue", "plan": false, "approve": [], "latitude": 0,
                   "route_ceiling": "standard", "parallel": 8,
                   "merge_bar": ["regression_test", "small_diff", "no_api_change"]},
    "feature":    {"description": "Add one capability to a system that exists, from a spec. Several issues across the repos it touches.",
                   "intake": "plan", "plan": true, "approve": ["plan"], "latitude": 0.25,
                   "route_floor": "standard", "parallel": "graph",
                   "merge_bar": ["acceptance_checks", "docs", "version_bump"],
                   "goal_check": "acceptance"},
    "new-system": {"description": "Build something that does not exist yet, from a goal statement, in phases that each end runnable.",
                   "intake": "phases", "plan": true, "approve": ["phase"], "latitude": 0.5,
                   "route_floor": {"design": "complex", "skeleton": "complex", "default": "standard"},
                   "parallel": {"skeleton": 1, "default": "graph"},
                   "merge_bar": ["design_conformance", "e2e_green", "phase_runnable"]}
  }
}
```

The planner makes a task from an issue this way:

| Task field | From the issue |
|---|---|
| `external_ref`, `repo` | `github:<owner>/<repo>#<number>` and its repo |
| `title`, `description` | The issue's title and body, treated as untrusted |
| `acceptance_checks` | The body's `## Acceptance checks` list, else the registry's checks for the repo |
| `task_type`, `complexity`, `max_tier` | The labels `type:<task type>`, `complexity:<low, medium or high>` and `max-tier:<tier>`, within the modality's floor and ceiling |
| `depends_on` | `Depends on: <issue>` lines in the body, plus the tracker's blocked-by links |
| `goal_id` | The goal |

`config/repos.json`, the repo registry, lists the repos the meta-harness may
change, with each one's default branch, default acceptance checks and merge
policy. For this project they are `davecthomas/ai-api-unified`,
`davecthomas/ai-api-unified-http` and `davecthomas/meta-harness`.

A task waits in `blocked` until every issue it depends on is closed. Tasks
with no open dependency run in parallel, within the modality's parallelism and
the repo-area locks. The planner refuses a dependency cycle and shows its
issues. A task that waited on another repo's change uses that repo's merged
commit.

Operators publish releases and deploy. An issue labeled `meta-harness:operator`
belongs to its goal, but the meta-harness never runs it: the console lists it
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

A goal is `met` when its success criteria pass, and `stalled` when every open
issue is blocked by a failed issue or waits on an operator issue, which raises
an alert. Its `max_usd` covers every task it queued; once spent, the scheduler
admits no more of its tasks and the goal ends `budget_exhausted`.

#### Latitude

A goal's `latitude`, from 0 to 1, sets how much scope the planner may add on
its own, the way a sampling temperature sets how far a model strays from the
likeliest token. It governs the planning task's prompt, the drafts the planner
accepts, and the loop's decision step.

| Latitude | The planner may | Example |
|---|---|---|
| 0 | Only the issues the goal names or people labeled. It drafts none, and it refuses a draft outside them. A `fix` goal at 0 is a pure queue | "Fix these twelve issues" |
| 0.25 | Draft the issues the statement needs and nothing beyond them. A draft must trace to a sentence in the statement | "Add the `route_ceiling` option" |
| 0.5 | Draft what the statement needs, plus the work it finds necessary on the way: tests the change needs, a doc the change makes wrong, a refactor the change needs to land | "Build the review loop" |
| 0.75 | Also draft improvements it finds in the goal's repos that serve the statement, as separate issues the operator sees in Inputs before they run | "Make the sample's end-to-end check faster and more thorough" |
| 1 | Keep going on its own: when the criteria are met it proposes the next goal from what it learned, and continues under the same budget until the budget or the operator stops it | "Improve the meta-harness" |

Above 0.5 every draft carries a `latitude_reason`, one line on how it serves
the statement, shown beside the issue in the console. Latitude changes only
what the planner may propose; the merge bar, the budget, the repo list and the
run service's sole write access to the code host stay fixed. The defaults are
`fix` 0, `feature` 0.25 and `new-system` 0.5, and an operator turns the dial
on the Goal view while the goal runs.

#### Planner

The planner owns the goal loop, the `goal`, `plan` and `issue_link` tables and
the issue dependency graph. It is the only component that talks to an issue
tracker, through one adapter interface:

| Adapter operation | Purpose |
|---|---|
| `list_open(repo, label)` | The open issues with the goal's label in one repo |
| `read(ref)` | One issue: title, body, labels, state, and the issues that block it |
| `create(repo, draft)` | Create an issue from a planning run's draft: title, body, labels, dependencies. Returns its `ref` |
| `comment(ref, text)` | Record a refusal, an outcome or a plan on the issue |
| `label(ref, add, remove)` | `meta-harness:failed`, `meta-harness:operator`, the goal label |
| `close(ref)` | Close an issue whose pull request merged without `Closes`, or an operator issue the console closed |
| `update(ref, state)` | Write a task's state back to the issue, as labels or the tracker's status field, with the pull request link |
| `webhook(payload)` | Turn a tracker's webhook into `issue_opened`, `issue_edited`, `issue_labeled`, `issue_closed` events, so the Inputs view and the goal loop never poll |
| `sweep(repo, label, since)` | Issues changed since a time, for the reconciliation that catches a missed webhook |

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
`linear:<identifier>`. Each repo in `config/repos.json` names its tracker, and
for Jira or Linear the project or team and the field or label that names an
issue's repo. The GitHub adapter comes first, over the GitHub App; Jira and
Linear adapters plug into the same interface.

A `feature` or `new-system` goal's issues come from a planning task,
`task_type: plan`, which the planner tickets with the goal statement, the
modality and, for a later phase, what earlier phases built. The planning run
reads the goal's repos and drafts issues with its `create_ticket` tool, each
in the repo it changes, labeled with the goal's label, with acceptance checks
and `Depends on:` lines. After the run the planner checks each draft against
the goal's repos and the modality's rules, refuses the rest with reasons, and
stores the plan. The goal waits in `planning` until an operator approves the
plan in the console, and then the planner creates the issues through the
adapter. A `fix` goal never plans. When a `new-system` phase ends with its
criteria unmet, or a failed task leaves dependents blocked, the planner
tickets a planning task with the failure as input, and that plan goes through
the same approval.

## Data model

| Owner | Entity | Fields |
|---|---|---|
| Task service | task | id, caller, external_ref, repo, task_type, parent_task_id, goal_id, depends_on, pr_url, head_sha, priority, spec, acceptance_checks, created_at, status (including `blocked`, `ready` and `review_unresolved`) |
| Planner | goal | id, caller, modality, modality_source (classifier, operator), modality_confidence, repos, label, statement, success_criteria, latitude, max_usd, priority, status (planning, active, paused, met, stalled, budget_exhausted, canceled), phase, created_at, closed_at |
| Planner | plan | goal_id, phase, drafted_issues, refused_drafts, approved_by, approved_at, created_refs |
| Planner | issue_link | ref (tracker-qualified), goal_id, task_id, repo, state, depends_on, last_webhook_at |
| Meta-harness run service | run | id, task_id, attempt_no, kind (initial, fallback, followup, retry), candidate, harness_profile, profile_version, model, model_policy, image, sandbox_id, state, started_at, running_at, ended_at, end_reason, fallback_reason |
| Meta-harness run service | routing_decision | id, task_id, attempt_no, request_id, route_tier, route_method, route_confidence, model_pin, candidates (the tier's ordered tier candidates), thinking, config_id, config_version, classifier_usd, route_floor, route_ceiling, explored, est_usd_max (from `preview_route()`'s `usd_max`), created_at |
| Meta-harness run service | operator_review | review_task_id, operator, claimed_at, verdict, findings, submitted_at, released_at |
| Meta-harness run service | config_version | kind (tiers, profiles, task_policy), version, document, effective_at |
| Pool manager | sandbox | id, image, executor, executor_ref, state (provisioning, warm, leased, kept, draining, unhealthy, destroyed), created_at, destroyed_at, destroy_reason |
| Pool manager | lease | run_id, sandbox_id, harness_session_id, session_state (new, kept, restored), granted_at, expires_at, released_at, release_reason |
| Meta-harness event service | event | event_id (UUID, unique), position (assigned on insert, monotonic), event_type, schema_version, time_utc, source (task, scheduler, dispatcher, route, run, workflow, pool, analyzer), actor, run_id, task_id, session_id, request_id, seq, trace_id, config_id, config_version, tags (jsonb), payload (jsonb) |
| Meta-harness event service | event_type | name, schema_version, json_schema, retention_class |
| Metering service | model_call | run_id, seq, turn, purpose (turn, compaction, auxiliary), tier, method, engine, model, thinking, input_tokens, cached_input_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens, usd, latency_ms |
| Metering service | attempt_usage | run_id, usd_total, calls, input_tokens, cached_input_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens, cache_hit_rate, active_hours |
| Metering service | check_result | task_id, check_name, kind (ci, acceptance, merge), passed, observed_at |
| Metering service | task_outcome | task_id, goal_id, ci_share, merged, review_rounds, review_loop_usd, total_usd, wasted_usd, active_hours, dollar_hours, closed_at |
| Metering service | spend | caller, period, cap_usd, spent_usd |
| Harness analyzer | proposal | id, kind (tier, profile, task_policy), evidence, proposed_change, status (open, approved, rejected), decided_by, decided_at, config_pr_url |

The `run` row records the candidate a run started on, and `model_call` records
which model answered each turn, which the harness analyzer reads. The `event`
table is indexed on `(run_id, position)`, `(run_id, time_utc)` and
`(event_type, time_utc)` and partitioned by time and retention class. The
metering tables are projections of it and can be rebuilt by replaying it.

Stores outside Postgres have one owner each: the scheduler's repo-area locks,
the run service's Temporal namespace and code-host rate-limit buckets, the
pool manager's artifact store, and the HTTP Gateway's run grants and per-run
spend counters.

## Observability

Meta-harness domain events go to the event service, and logs, traces and
metrics go to OpenTelemetry. Every domain event carries its trace id, so the
two join.

### Event service

The event service is the record of what happened in every task and run. It
owns the `event` table, the per-type schema registry, position assignment,
partitions, retention and the commit notification, and it calls no other
component. ai-api-unified keeps its own library events and cost ledger.

| Operation | Contract |
|---|---|
| `append(events)` | Idempotent on `event_id`. The event service validates each meta-harness domain event against its event type's registered schema in the schema registry. The event service rejects an unknown event type or invalid payload back to the producing meta-harness service as a bug. The event service client library never retries a rejected append. |
| `read(run_id, from_position)` | A meta-harness run's meta-harness domain events in `position` order, paged |
| `tail(filter, from_position)` | A live stream of meta-harness domain events matching a filter (run, task, type, source), in `position` order, resumable from any position |

Meta-harness services append through the event service client library:

| Producer | Events |
|---|---|
| Task service | `task_created`, meta-harness task status changes, received code-host PR webhooks |
| Planner | `goal_created`, `goal_classified`, `modality_changed`, `latitude_changed`, `plan_drafted`, `plan_approved`, `issues_created`, `issue_joined`, `task_blocked`, `task_unblocked`, `criteria_evaluated`, `replan_ticketed` and `goal_closed`, and the tracker webhooks it received |
| Meta-harness scheduler | Admission decisions, with the reason the meta-harness scheduler holds a meta-harness task |
| Meta-harness run service: RunTask workflow, `route` and run activities | `task_routed` from the run service `route` activity result. One `attempt_finished` per run from the run service run activity Collect result, joined to its pool manager sandbox lease by `run_id` |
| Meta-harness run service: run activity (Wait step) | The per-run sandbox's harness server Run API event stream: `turn_started`, `model_call` (with the HTTP Gateway's model-token usage and running total), `tool_call`, `tool_result`, `file_changed`, `tests_run`, `guardrail_blocked`, `compacted`, `turn_finished`, `run_finished`. The run service run activity stays the only consumer of each per-run sandbox's harness server Run API stream. Harness server Run API heartbeats are not stored. |
| Meta-harness run service: RunTask Temporal workflow | Run state transitions, `check_completed` from the PR signals the RunTask workflow receives, `review_submitted` from each meta-harness review task's run (verdict, findings, reviewed head SHA), `review_started`, `operator_review_claimed`, `operator_review_submitted` and `operator_review_released` with the operator as actor, `review_ticketed` and `fix_ticketed`, `pr_merged`, and `task_closed`. Operator actions the RunTask workflow receives (cancel, kill switch), as audit events with the actor |
| Pool manager | `lease_granted` (with `session_state`), `lease_released`, `session_saved`, `session_restored`, `sticky_handoff`, `sandbox_recycled`. The pool manager lease record names the per-run sandbox for each run. |
| Harness analyzer | `proposal_opened`, and operator approvals and rejections of harness analyzer proposals as audit events with the actor |

The client library buffers events in its own outbox and retries, which is safe
because `append` is idempotent on `event_id`, so an event service outage never
blocks a run.

After an event service append commits, the event service
sends a commit notification, Postgres `NOTIFY`, with the run id and the new
highest `position`. The commit notification contains no meta-harness domain
event data and is internal to the event service: its listeners are the event
service's own `tail` handlers, which fetch the new event rows and stream them
to their callers. A missed commit notification costs latency, not data, because
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
than deleting rows. The event service keeps run, routing, attempt and audit
meta-harness domain events long.

### Signals

| Signal | Source | Carried by |
|---|---|---|
| Meta-harness domain events | The meta-harness producer services above | Meta-harness event service |
| OpenTelemetry traces | One OpenTelemetry trace per meta-harness task, spanning routing, scheduling, each meta-harness run, each harness server Run API call and each HTTP Gateway call | OpenTelemetry; `task_id` and `run_id` on every span, and the trace id on every meta-harness domain event |
| OpenTelemetry metrics | Task queue depth, meta-harness scheduler admission decisions, per-run sandboxes by state, pool manager lease latency, run durations and turns per run. Model calls by routing tier, spend by Task API caller, HTTP Gateway latency and error rate | OpenTelemetry metrics |
| Operational logs | Operational text, structured, with `task_id`, `run_id` and trace id on every line; prompt and response content only when configured | Existing log pipeline |

### Console

The operator console is the cloud web app operators steer the meta-harness
from. Operators sign in with GitHub, against an allowlist of accounts. The
console owns no store: it reads the task service, the planner, the run
service, the event service, the metering service and the pool manager through
their interfaces, and sends each command to the owning service, which records
it as an audit event with the operator as actor.

The console serves many parallel tasks pursuing several goals at once.
Every view drills down to the next, and every list filters by goal, modality,
repo, time, harness profile, model, routing tier, task type and state.

| View | Shows | Actions |
|---|---|---|
| Overview | Every active goal with its modality, progress against its success criteria, spend against budget and tasks in flight. A "needs you" list: plans and phases to approve, operator issues (releases, deploys) only an operator can close, unresolved reviews, alerts. Run capacity, HTTP Gateway health and the kill switch | Create goal, kill switch |
| Inputs | The queued GitHub issues across every registered repo, by repo and by goal: each one's state (queued, blocked and on what, running, PR open, in review, merged, failed), issues no goal has picked up, and which repos each goal is working in now | Add an issue to a goal, drop one |
| Goal | The goal's statement, its modality (with the classifier's confidence, or "set by operator") and latitude dial, its issue dependency graph across repos, the plan or the phase timeline with each phase's criteria, every success criterion's live state, spend over time, and its tasks. Each planner-drafted issue shows its `latitude_reason` | Approve plan or phase, set latitude, pause, resume, cancel |
| Tasks | One task: its issue, every ai-api-unified router routing decision, every run including fallbacks, every check result, the diff, the pull request, each review round's verdict and findings and the fix task it ticketed, the merge, and cost per run | Cancel |
| Run | Live timeline of the run's events: each turn's model call (model, thinking, tokens, cost, latency), tool calls, file changes, test runs, compactions and guardrail blocks, the OpenTelemetry trace, and the per-run sandbox | Cancel |
| Live runs | Every run in flight across every goal: task, repo, model, turn, spend as it accrues, time since its last event | Cancel |
| Routing | Tier picked versus the candidate whose run finished, classifier confidence, floors and ceilings by modality, moves between candidates and escalations | |
| Pool | Per-run sandboxes by image and state, warm targets versus actual, lease latency, health failures | |
| Spend and performance | Cost by goal, modality, repo, model, harness profile and routing tier. Cost per merged pull request and time to merge, by modality. Dollar-hours per completed task over time. Cache hit rate | |
| Configuration | The active routing tiers, harness profiles, modalities, task policy and repo registry, their version history, and the harness analyzer's pending proposals | Approve or reject a proposal |
| Audit | Every operator action and configuration change | |

A task's runs, fallbacks and retries included, run one after another, and the
Tasks view lists them in order with the task's review and fix tasks under
them.

The console reads runs from the event service: `read` for a run's history,
then `tail` from the last position, so a run in progress opens at any point
with no gap, and a slow console falls behind without slowing a producer. Tool calls, tool results, file changes, test runs,
guardrail blocks, compactions, model tokens and spend are live as they
happen, from the `model_call` events recorded off the harness server Run API.
Model text arrives once per turn, and token by token once the HTTP Gateway
streams.

Event payloads contain repository content. The console
scrubs secrets from the events it reads. The HTTP Gateway's per-turn
redaction applies to what the model receives.

### Alerts

Meta-harness alert rules tail the event service and read OpenTelemetry metrics.
They fire on: a meta-harness run stalled past its harness server heartbeat
window, pool manager lease TTL expiries, warm sandbox pool below target for
longer than the sandbox provisioning time, HTTP Gateway error rate or latency
past its SLO, a meta-harness task failing with every routing tier exhausted,
a meta-harness task ending `review_unresolved`, a goal `stalled`, a claimed review round
waiting on an operator past `review.operator_alert_minutes`, spend past 80%
of any cap, usage reconciliation gaps over 5%, and a harness
profile and model whose cache hit rate falls below its configured floor.

## Security

- A GitHub App is installed on each registered repo. A per-run sandbox gets a
  read-only, repo-scoped access token for its clone, minted per run and
  expiring within an hour. The run service writes to the code host with the
  app's own token: it pushes branches, opens pull requests, posts reviews and
  merges, and the planner creates and updates issues.
- Secret Manager mounts each secret only into its owner service's
  pods. The HTTP Gateway holds the model provider API keys for every model call
  a run makes. The run service and the planner hold one more: a classifier
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
- Every meta-harness task launch, review and fix ticket, operator
  review claim and verdict, merge, cancel and configuration change is a meta-harness domain event in the event service,
  recorded by the meta-harness service that received it, with its actor.

## Deployment

The meta-harness runs on Google Cloud, one project per environment. Workload
Identity gives each managed service one owning component. The ai-api-unified
library runs in-process in the run service and HTTP Gateway pods.

```mermaid
flowchart TB
    USERS[Operators and Task API callers]
    CH[Code host]
    REG[Package registries]
    PROV[Model providers]
    TC[Temporal Cloud<br/>run service Temporal namespace]

    subgraph GCP["GCP project, one per environment"]
        XLB[External HTTPS LB<br/>Identity-Aware Proxy for Task API and operator console,<br/>code-host webhook route without IAP]
        subgraph VPC["Private VPC"]
            subgraph GKE["GKE Autopilot cluster, private"]
                subgraph CTRL["namespace: control, one Deployment and HPA per meta-harness component"]
                    TS[meta-harness task service<br/>Task API]
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
    end

    USERS --> XLB
    CH -->|code-host webhooks, signature verified by the task service| XLB
    XLB --> TS
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
```

### First cloud deployment

A smaller deployment runs the same services without GKE and the pool manager:
managed services, plus one VM for the per-run sandboxes.

| Component | Runs on | Notes |
|---|---|---|
| Operator console | Cloud Run | GitHub sign-in |
| Task service, planner, event service | Cloud Run, Cloud SQL for PostgreSQL | The Task API, the goal loop and the GitHub issue tracker adapter, code-host webhooks, the event table |
| HTTP Gateway | Cloud Run | ai-api-unified-http |
| Temporal | Temporal Cloud | The run service namespace |
| Run service worker, egress proxy, per-run sandboxes | One Compute Engine VM with Docker | The Docker executor backend, as a systemd service that restarts on failure and on reboot |
| Code host | A GitHub App on the registered repos | Issue and pull request webhooks keep the Inputs view live without polling; read-only clone tokens per run; the run service's app token for every code-host write |

GKE and the pool manager replace the VM behind the same run service
interfaces.

### Compute

- One private, regional GKE Autopilot cluster.
- One Deployment per meta-harness component: the
  meta-harness task service, the meta-harness scheduler, the meta-harness run
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
| Temporal Cloud | The meta-harness run service's Temporal namespace, reached over Private Service Connect. RunTask workflow history, timers and signals. | RunTask workflows stop advancing, but per-run sandboxes keep running and finish their work. When Temporal Cloud returns, run service activities retry and re-attach to the harness server by `run_id` and `Last-Event-ID`. A run whose pool manager sandbox lease TTL expires during the outage is lost, and the meta-harness run service retries it as a new attempt. |
| Memorystore for Redis | Standard tier with a replica. One key prefix per owning component. The HTTP Gateway keeps HTTP Gateway run grants and HTTP Gateway per-run spend counters. The meta-harness scheduler keeps repo-area locks. The meta-harness run service keeps code-host API rate-limit token buckets. The event service keeps a commit notification fallback, unused while Postgres `NOTIFY` suffices. | Memorystore failover keeps the data. If Memorystore loses the HTTP Gateway's spend counters or run grants, the HTTP Gateway fails closed. The HTTP Gateway then rejects model turns for any run it cannot verify or meter. Those runs end, and the meta-harness run service retries them as new attempts. If Memorystore loses the meta-harness scheduler's repo-area locks, the meta-harness scheduler stops admitting tasks until it rebuilds the locks from the run service's active runs. |
| Cloud Storage | One bucket per owning component. The pool manager's Cloud Storage artifact bucket holds pi session directories under `harness_session_id` and per-run sandbox logs. The meta-harness run service's bucket holds run artifacts, and the event service's bucket holds event exports. | The pool manager's sandbox drain step retries artifact collection. A per-run sandbox stays in `draining` until the pool manager saves the per-run sandbox's artifacts to the pool manager's bucket or a limit passes. Follow-up tasks that need a saved pi session wait. |
| Artifact Registry | Every container image: the per-run sandbox harness image and one image per meta-harness control-plane component. | Pods cannot pull uncached images. Running meta-harness services and warm per-run sandboxes keep running, and image rollover pauses. |
| Secret Manager | Secret Manager mounts each secret only into its owning component's pods. Model provider API keys go to the HTTP Gateway, except the ai-api-unified router classifier's provider API key. The classifier's provider API key goes to the run service and the planner. The meta-harness run service also receives an HTTP Gateway API key. The run service run activity uses that HTTP Gateway API key to request and revoke HTTP Gateway run grants. | Secrets load into pods at pod start, so a Secret Manager outage blocks new pods only. |
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
- An external HTTPS load balancer with
  Identity-Aware Proxy fronts both the operator console and the task service
  Task API. Programmatic Task API callers present a Google service account
  identity to IAP and their per-caller Task API caller key to the Task API.
  Code-host webhooks cannot present IAP credentials, so the code-host webhook
  route uses its own load balancer backend without IAP, and the task service
  verifies the code host's webhook signature on every delivery.
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
  Google Cloud permissions. The HTTP Gateway API key that the run service run
  activity uses to request and revoke run grants reaches only the meta-harness
  run service pods, through Secret Manager.

### Observability

An OpenTelemetry collector DaemonSet sends traces to Cloud Trace and logs to
Cloud Logging. Cloud Monitoring holds the metrics that drive the horizontal pod
autoscalers and the alerts. The meta-harness event service
remains the record of meta-harness domain events, and domain events join to
OpenTelemetry traces by trace id.

### Local development

Docker Compose runs Postgres, Redis, the Temporal dev server and every
meta-harness service. Per-run sandboxes use the Docker executor backend behind
the same pool manager executor interface, from the same images, and a local
egress proxy enforces the same per-run sandbox egress allowlist. Each local
per-run sandbox is on its own internal Docker network, which has no route
out, and the local egress proxy is attached to each one. Docker publishes no
port from an internal network, so the run service reaches each harness server
through the local egress proxy's control listener.

## Build plan

Steps after **next** are plans, and they change as the build goes.

| Step | State | Where |
|---|---|---|
| pi model client extension: pi's model calls through the HTTP Gateway | built | `images/harness-pi/extension` |
| Harness server and the harness server Run API | built | `images/harness-pi/server` |
| Each run as a meta-harness run service Temporal workflow (the RunTask workflow), with re-attach to the harness server on retry and cancel | built | `samples/temporal-run-task` |
| HTTP Gateway run grants: model pin and spend limit per run | built | ai-api-unified-http 1.18.0, `samples/temporal-run-task` |
| Per-run sandbox: the harness server in a Docker container per run, which the run service run activity starts and removes with the Docker executor backend | built for local development | `images/harness-pi/Dockerfile`, `samples/temporal-run-task` |
| Per-run sandbox egress allowlist: each per-run sandbox on its own internal Docker network, with a local egress proxy that lets it reach only the HTTP Gateway, the code host and package registries | built for local development | `samples/temporal-run-task/egress-proxy`, `make egress-check` |
| A zero-cost end-to-end check: the RunTask workflow against a fake HTTP Gateway that makes no model calls, on every pull request | built | `samples/temporal-run-task/e2e.sh`, `make e2e`, `.github/workflows/e2e.yml` |
| Provider-neutral tool results: one tool-result shape the HTTP Gateway converts for each model provider | built | ai-api-unified-http 1.19.0, `images/harness-pi/extension` |
| The RunTask workflow's run loop over the ai-api-unified router's candidates, with a run service `route` activity, escalation, pins and a task-wide spend limit | built | ai-api-unified 3.30.0 (`AiRouteClassifier`), `samples/temporal-run-task`, `make fallback-demo` |
| Repo work in a run: each run starts from a fresh clone of the task's repo, pi's `bash` tool runs the acceptance checks, and the run service's Publish step commits pi's changes, pushes the branch and opens the pull request with `Closes <ticket>`. The per-run sandbox holds no code-host credential | built for local development | `samples/temporal-run-task`, `make repo-demo`, `make run TICKET=owner/name#N` |
| Operator console, first form: a cloud web app you sign in to with GitHub, over the task service, the planner with its GitHub issue tracker adapter, and the event service on Cloud SQL. Goals, the Inputs view of queued GitHub issues across the registered repos, tasks, live runs, spend, and the actions start, pause, cancel and kill switch | **next** | |
| Standing cloud deployment: console, task service, planner and event service on Cloud Run, Temporal Cloud, and one Compute Engine VM running the run service worker, the egress proxy and the per-run sandboxes with the Docker executor backend. The meta-harness runs with no Claude Code session and no terminal | planned | |
| Meta-harness review loop and merge: a review task on another model, fix tasks, and the merge at the reviewed head SHA, each round visible in the console | in progress | `samples/temporal-run-task` branch `feat/review-loop`; `submit_review` built in `images/harness-pi/extension` |
| Goals with the `fix` modality: a label across the registered repos feeds a standing queue, and the console shows each issue's state | planned | |
| Goals with the `feature` and `new-system` modalities: the planning task, plan and phase approval in the console, and goal success criteria | planned | |
| Self-hosting: a `new-system` goal whose repos are ai-api-unified, ai-api-unified-http and meta-harness, steered from the console | milestone | |
| Pool manager, kept and restored pi sessions | later | |
| Meta-harness owned pi tools, the `bash` command allowlist, pi compaction handler, `guardrail_blocked` harness server Run API events | later | |
| Operator review | sketch | |
| HTTP Gateway: `route` in the conversation request body, so on a run grant that pins a model one call can run on a tier band capped at the pinned model's tier. pi agent turns without `route` stay pinned | later | ai-api-unified-http |
| Streaming: a `send_conversation` form in ai-api-unified that yields typed events and commits to one model at its first delta, and a server-sent-events form of the HTTP Gateway conversation endpoint, so the console shows model text token by token | optional | ai-api-unified, ai-api-unified-http |
| Pool manager on GKE, metering service, harness analyzer, meta-harness scheduler as its own service | sketch | |

The zero-cost end-to-end check runs on every pull request, so each later build
step is proven end to end before it spends money on model calls.

The steps up to the self-hosting milestone come first, in this order. After
them, the meta-harness builds the rest of itself while the operator watches
from the console. The console comes before the review loop is finished, so the
operator sees every review round from the first. Once pull requests merge
without a person, a goal's issues run in order across repos, and the planner
writes each next phase. Self-hosting needs stronger models than the sample's
Haiku 4.5 and gpt-4o-mini, which cost more, so each goal has its own spend cap.
