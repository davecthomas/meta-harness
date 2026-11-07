# Meta-Harness Technical Design

## State of the build

Each pull request that lands a build step updates that step's row. Every build
step past the current build step is a sketch, open to change when building it
teaches something.

| Step | State | Where |
|---|---|---|
| pi model client extension: pi's model calls through the HTTP Gateway | built | `images/harness-pi/extension` |
| Harness server and the harness server Run API (section 8.2) | built | `images/harness-pi/server` |
| Each run as a meta-harness run service Temporal workflow (the RunTask workflow), with re-attach to the harness server on retry and cancel (section 7) | built | `samples/temporal-run-task` |
| HTTP Gateway run grants: model pin and spend limit per run (section 6.4) | built | ai-api-unified-http 1.18.0, `samples/temporal-run-task` |
| Per-run sandbox: the harness server in a Docker container per run, which the run service run activity starts and removes with the Docker executor backend (sections 6.3, 8.1 and 8.3) | built for local development | `images/harness-pi/Dockerfile`, `samples/temporal-run-task` |
| Per-run sandbox egress allowlist: each per-run sandbox on its own internal Docker network, with a local egress proxy that lets it reach only the HTTP Gateway, the code host and package registries (section 15, Local development) | built for local development | `samples/temporal-run-task/egress-proxy`, `make egress-check` |
| A zero-cost end-to-end check: the RunTask workflow against a fake HTTP Gateway that makes no model calls, on every pull request | built | `samples/temporal-run-task/e2e.sh`, `make e2e`, `.github/workflows/e2e.yml` |
| Provider-neutral tool results: one tool-result shape the HTTP Gateway converts for each model provider | built | ai-api-unified-http 1.19.0, `images/harness-pi/extension` |
| The RunTask workflow's run loop over the ai-api-unified router's candidates, with a run service `route` activity, escalation, pins and a task-wide spend limit (section 6.3) | built | ai-api-unified 3.30.0 (`AiRouteClassifier`), `samples/temporal-run-task`, `make fallback-demo` |
| Repo work in a run: each run starts from a fresh clone of the task's repo, pi's `bash` tool runs the acceptance checks, and the run service's Publish step commits pi's changes, pushes the branch and opens the pull request with `Closes <ticket>`. The per-run sandbox holds no code-host credential (principle 9, sections 4, 6.3 and 14) | built for local development | `samples/temporal-run-task`, `make repo-demo`, `make run TICKET=owner/name#N` |
| Operator console, first form: a cloud web app you sign in to with GitHub, over the task service, the planner with its GitHub issue tracker adapter, and the event service on Cloud SQL. Goals, the Inputs view of queued GitHub issues across the registered repos, tasks, live runs, spend, and the actions start, pause, cancel and kill switch (sections 11 and 13) | **next** | |
| Standing cloud deployment: console, task service and event service on Cloud Run, Temporal Cloud, and one Compute Engine VM running the run service worker, the egress proxy and the per-run sandboxes with the Docker executor backend. The meta-harness runs with no Claude Code session and no terminal (section 15, First cloud deployment) | planned | |
| Meta-harness review loop and merge: a review task on another model, fix tasks, and the merge at the reviewed head SHA, each round visible in the console (section 7, Review loop) | in progress | `samples/temporal-run-task` branch `feat/review-loop`; `submit_review` built in `images/harness-pi/extension` |
| Goals with the `fix` modality: a label across the registered repos feeds a standing queue, and the console shows each issue's state (section 11, Goals and modalities) | planned | |
| Goals with the `feature` and `new-system` modalities: the planning task, plan and phase approval in the console, and goal success criteria (section 11) | planned | |
| Self-hosting: a `new-system` goal whose repos are ai-api-unified, ai-api-unified-http and meta-harness, steered from the console | milestone | |
| Pool manager, kept and restored pi sessions (section 8.4) | later | |
| Meta-harness owned pi tools, the `bash` command allowlist, pi compaction handler, `guardrail_blocked` harness server Run API events (section 4) | later | |
| Operator review (section 7) | sketch | |
| Pool manager on GKE, metering service, harness analyzer, meta-harness scheduler as its own service (sections 8.4, 9, 10) | sketch | |

The zero-cost end-to-end check runs on every pull request, so each later build
step is proven end to end before it spends money on model calls.

The steps up to the self-hosting milestone come first, in this order, so the
meta-harness builds the rest of itself and the operator watches it from the console. The console comes before the review loop
is finished because the review loop's rounds, like every later piece, must be
visible as they land. Once pull requests merge without a person, a goal's
issues can run in order across repos, and the planner writes each next phase
of issues. Self-hosting tiers need stronger models than the sample's Haiku 4.5
and gpt-4o-mini, so each goal has its own spend cap.

## 1. Scope

The meta-harness runs AI coding tasks in parallel, each on its own harness profile and
model. A team sets goals, and each goal queues coding tasks from GitHub issues across
one or more repos (section 11, Goals and modalities). For each task the
meta-harness picks the harness-profile-and-model combination that fits the task's difficulty,
runs that combination in its own isolated per-run sandbox through the harness server Run API, meters every
model call, and records the outcome, usually a pull request.

Tasks run independently, on different harness profiles and on models from
different model providers, so one task's failure or one model provider's outage does not
stop the others. A developer driving one coding-agent session works through one task
at a time. The meta-harness runs dozens of tasks at once, as many as per-run sandbox
capacity, caller quotas and spend caps allow (section 10). Adding per-run sandboxes and
HTTP Gateway replicas raises that limit with no change in design.

### Who uses it

- **Teams and their tools** submit tasks through the task service Task API: a person, a CI
  job, or an issue-tracker integration. Each Task API caller has a Task API caller key that sets the caller's
  concurrency quota, the caller's daily spend cap and the repos the caller may change.
  Most tasks come from goals: a goal's label on GitHub issues across several
  repos queues them (section 11, Goals and modalities). A direct Task API call
  is the exception.
- **Operators** steer the meta-harness from the operator console, a cloud web
  app: they create goals, approve plans, watch the queued issues, tasks, runs
  and model calls with their cost, and can pause a goal, cancel a run or stop
  every run with the kill switch. The console is the control point; no
  operator drives the meta-harness from a terminal or a coding-agent session.
- **Configuration owners** approve or reject the harness analyzer's proposals.
  An approved harness analyzer proposal becomes a pull request against `config/`.

### Tasks in, pull requests out

Each task names a repo, a task type, a priority, a title and description, and the
acceptance checks that define success. A task may also name the paths it touches,
a complexity hint, a highest ai-api-unified router tier, a dollar cap, or a pin to one harness profile and
model (section 11). Tasks beyond the current concurrency limit wait in priority order until
a run slot opens (section 10).

For each task the meta-harness opens one pull request, labeled
`ai:meta-harness`, and records the outcome: the ai-api-unified router's routing decision, every attempt
with its harness profile, model, thinking level, time and cost, and whether the pull
request merged.

No person has to review a meta-harness pull request. The meta-harness review
loop (section 7) reviews each pull request with a meta-harness review task and
tickets failed code-host checks and review findings as meta-harness fix tasks
on the same branch. The loop repeats until a review comes back clean, and the
meta-harness run service then merges the pull request. An operator watching
the operator console can join any review round, and that round then waits for
the operator's verdict (section 7, Operator review).

### Components

The table groups the meta-harness system components the way the sequence diagrams box them. Each
component owns its data (principle 8).

| Component | What it does | Code |
|---|---|---|
| **Meta-harness control plane** | | `src/meta_harness/` in this repo |
| Task service | Accepts, de-duplicates and tracks goals and tasks. Serves the task service Task API, turns a goal's issues into tasks, evaluates goal success criteria, and receives code-host webhooks. | `task_service/` |
| Planner | Turns a goal into issues, and issues into tasks. Runs the goal loop's Take work, Evaluate and Decide steps, tickets planning tasks, creates the issues a planning run drafted, keeps the issue dependency graph, and syncs both ways with the issue trackers: it pulls in issues people wrote and writes back each task's state and outcome. It reaches every tracker through one issue tracker adapter interface: GitHub Issues first, Jira and Linear later (section 11, Planner). | `planner/` |
| Meta-harness scheduler | Decides which task runs next: goal priority, modality rules, caller quotas, spend caps, repo-area locks, and the dependencies within a goal. | `scheduler/` |
| Meta-harness run service | Runs each task from admission to outcome as a durable Temporal workflow, the RunTask workflow. The meta-harness run service is the only caller of the `ai-api-unified` router. It watches the per-run sandbox and closes the task. It runs the meta-harness review loop on the task's pull request and merges the pull request when a review is clean. | `run_service/` |
| Pool manager | Supplies per-run sandboxes: leases them by run id, keeps a warm pool of sandboxes, checks sandbox health, recycles sandboxes. | `pool/` |
| Event service | Keeps the ordered record of every meta-harness domain event. Every other meta-harness component appends and reads meta-harness domain events through the event service. | `events/` |
| Metering service | Computes cost, time and success for each attempt and task, including dollar-hours per completed task by harness profile, model, thinking level and task type. | `metering/` |
| Harness analyzer | Turns metering service evidence into proposed changes to ai-api-unified router tiers, harness profiles and task policy. | `analyzer/` |
| Operator console | The cloud web app operators steer the meta-harness from: goals and their modalities, the queued GitHub issues across every registered repo, tasks, live runs, review rounds, spend and per-run sandbox state. Its actions are create, pause and resume a goal, approve a plan or phase, cancel, the kill switch, joining a review round, and harness analyzer proposal approval, each sent to the owning meta-harness service. | `console/` |
| **ai-api-unified library**, in the meta-harness run service process | | the `ai-api-unified` repo |
| ai-api-unified router | Classifies a task with a direct call to the ai-api-unified router's classifier model, picks a tier, and returns the tier's ordered harness-profile-and-model candidates. The meta-harness run service's RunTask workflow tries them (section 6.3). | `AiRouterCompletions` |
| **Per-run sandbox**, one container per run | | `images/harness-pi/` in this repo |
| Harness server | Exposes the harness server Run API and runs pi in-process through pi's SDK. | `server/` |
| pi | The coding agent: pi's agent loop, pi's tools, pi's model context, the pi session. Third-party (pi.dev), used unmodified through pi's SDK. | `@earendil-works/pi-coding-agent` |
| pi model client extension | A pi extension that sends every model call pi makes to the HTTP Gateway. | `extension/` |
| **HTTP Gateway** | The only model endpoint a per-run sandbox can reach. Holds the provider API keys for agent model calls and the HTTP Gateway run grants. Pins each run's model, applies ai-api-unified guardrail checks, and enforces each run's HTTP Gateway run grant spend limit. | the `ai-api-unified-http` repo |
| **Outside the system** | Used by the meta-harness and built elsewhere. | |
| Code host | Repositories, pull requests, code-host checks, reviews and webhooks. | |
| Model providers | Cloud model APIs, or local models served by vLLM. | |
| Temporal Cloud, Google Cloud | The Temporal workflow engine and the Google Cloud infrastructure (section 15). | |

The guardrails library has no row in the table because it runs inside its
callers. The meta-harness run service and the pool manager call the guardrails
library to scope repository access and credentials per run and to enforce
caller and global spend caps. The HTTP Gateway screens untrusted text with
ai-api-unified's own guardrail checks.

`ai-api-unified` routes at both levels. At the task level, the ai-api-unified
router's classifier model reads the coding task and picks a tier, and the
tier's candidates are harness-profile-and-model combinations. The meta-harness
run service's RunTask workflow tries those candidates and escalates between
tiers (section 6.3). At the model-call level, pi makes every model call
through the pi model client extension and the HTTP Gateway, so ai-api-unified
routing, the ai-api-unified fallback chain, provider cooldowns, ai-api-unified
guardrail checks and the HTTP Gateway run grant spend limit apply to every
model token the pi coding agent spends.

### Non-responsibilities

- **Choosing models.** Difficulty classification, each tier's candidate list,
  the ai-api-unified fallback chain inside a model call, provider cooldowns and
  per-call pricing belong to `ai-api-unified`.
- **The agent loop.** pi runs the pi agent loop. The meta-harness configures pi through
  harness profiles and the pi model client extension.
- **Agent model access.** The HTTP Gateway holds the provider API keys for every
  model call a run makes, and no per-run sandbox holds one. The meta-harness run service holds
  a single provider API key that serves only the ai-api-unified router's classifier model (section 14).
- **Setting goals.** Operators decide what the meta-harness works toward. The
  meta-harness tickets meta-harness review tasks and meta-harness fix tasks
  for its own pull requests, and a planning task writes a goal's issues under
  its modality's rules (section 11, Goals and modalities).
- **Deploying and releasing.** The team's own deploy pipeline deploys a merged
  pull request. Publishing a package or tagging a release is an operator
  action, which a goal records as an operator issue (section 11).

### Where it runs

On Google Cloud: one GKE Autopilot cluster for the meta-harness services and the per-run sandboxes,
Cloud SQL for PostgreSQL, Memorystore for Redis, Cloud Storage and Temporal
Cloud. Local development runs the same images on Docker Compose. Section 15
covers both.

## 2. Principles

1. **Code never names a model or a harness profile.** `models.json` maps an ai-api-unified router difficulty
   tier to harness-profile-and-model candidates. Changing the mapping is a
   configuration change.
2. **Every model call goes through ai-api-unified.** The run service `route` activity calls the
   ai-api-unified router in-process to pick a task's tier and candidates. The harness server,
   through the pi model client extension, calls the HTTP Gateway's native conversation API with the run's HTTP Gateway run grant key.
   No per-run sandbox holds a provider API key. No meta-harness component translates a model call into another vendor's wire protocol.
3. **The harness server Run API drives the pi coding agent.** Start, follow up,
   stream events, status, cancel and usage are harness server Run API calls. No
   step uses a command line or parses terminal output.
4. **Fail at startup, not during a run.** Startup validates every harness
   profile and every ai-api-unified router tier candidate against the harness profile images and the
   `ai-api-unified` configuration. A missing model, harness profile or harness profile image stops the starting
   meta-harness service and names the setting.
5. **The meta-harness meters and records every run**, failures included. A failed attempt
   counts against cost and time.
6. **Durable by default.** A run lasts minutes to hours. Any meta-harness process can die
   and the run resumes from its last recorded RunTask workflow step.
7. **Harness variants are configuration.** A harness profile is a harness profile image, a
   prompt set, a pi tool set and a loop policy. Adding a harness profile is a configuration
   change, and the harness analyzer measures it like any other routing dimension.
8. **Every datastore has exactly one owner.** Every other meta-harness component reaches
   that data only through the owner component's interface. No meta-harness component holds a
   connection to a table it does not own. `ai-api-unified` owns no store.
9. **A per-run sandbox writes nothing on the code host.** It can read its task's repo. The
   meta-harness run service makes every code-host write from what the run produced, after
   the run: it pushes the branch, opens the pull request, posts a review and creates a
   planning task's tickets. Nothing pi runs can push, so a prompt injection cannot reach
   the code host either.

## 3. Architecture

Each arrow below is a call on the target component's exposed interface. The only arrows that end at a datastore
come from that datastore's owner component (principle 8).

```mermaid
flowchart TB
    CALLER[Task API callers]
    CH[Code host]
    PROV[Model providers<br/>cloud or local]

    subgraph CP["Meta-harness control plane"]
        TS[Task service<br/>Task API]
        SCHED[Meta-harness scheduler]
        RS[Meta-harness run service<br/>RunTask workflows, dispatcher activity,<br/>route activity, run activity]
        PM[Pool manager]
        EV[Event service]
        MET[Metering service]
        HA[Harness analyzer]
        CON[Operator console<br/>owns nothing]

        TSDB[(task)]
        SCDB[(repo-area locks)]
        RSDB[(run, routing_decision,<br/>config_version,<br/>RunTask workflow history)]
        PMDB[(sandbox, lease,<br/>pool manager artifact store)]
        EVDB[(event, event schema registry)]
        MEDB[(metering projections:<br/>model_call, attempt_usage,<br/>check_result, task_outcome, spend)]
        HADB[(proposal)]
    end

    subgraph AIU["ai-api-unified library and ai-api-unified-http service"]
        RT[ai-api-unified router<br/>ai-api-unified library, in the meta-harness run service,<br/>owns no store]
        GW[HTTP Gateway<br/>ai-api-unified-http]
        GWDB[(HTTP Gateway run grants,<br/>HTTP Gateway per-run spend counters)]
    end

    subgraph POOL["Per-run sandbox: one per run"]
        HS[Harness server<br/>harness server Run API]
        HX[pi + pi model client extension]
        HS --> HX
    end

    TS --- TSDB
    SCHED --- SCDB
    RS --- RSDB
    PM --- PMDB
    EV --- EVDB
    MET --- MEDB
    HA --- HADB
    GW --- GWDB

    CALLER -->|create, get, list, cancel task| TS
    CH -->|code-host PR, check, review webhooks| TS
    TS -->|signal PR event, cancel| RS
    SCHED -->|list queued tasks| TS
    SCHED -->|start RunTask workflow| RS
    SCHED -->|caller and global spend| MET
    SCHED -->|sandbox capacity| PM
    RS -->|set task status| TS
    RS -->|route activity: classify-only call, in-process| RT
    RT -->|classifier model call| PROV
    RS -->|run activity: lease, release, lookup sandbox| PM
    RS -->|run activity: request and revoke HTTP Gateway run grant| GW
    RS -->|run activity: Run API run, follow up, events, usage, cancel| HS
    PM -->|provision, probe, drain, destroy sandbox| HS
    HX -->|conversation turns, run grant key| GW
    GW --> PROV
    HX -->|push, open PR on code host| CH
    TS & SCHED & RS & PM & HA -->|append meta-harness domain events| EV
    MET -->|tail meta-harness domain events| EV
    HA -->|read meta-harness domain events| EV
    HA -->|metering queries| MET
    HA -->|approved change as a config PR| CH
    CON -->|read, tail meta-harness domain events| EV
    CON -->|get, list tasks| TS
    CON -->|run status, cancel, kill switch| RS
    CON -->|metering queries| MET
    CON -->|list, approve proposals| HA
    CON -->|sandbox pool status| PM
```

| Component | Objective | Owns | Exposes | Consumes | Scales by | Fails how |
|---|---|---|---|---|---|---|
| Task service | Accept, de-duplicate and track tasks. The task service Task API (section 11) is the task service's HTTP interface. | `task` | create (idempotent on `external_ref`), get, list, set status, cancel; the code-host webhook receiver | Meta-harness run service (signal PR events, cancel, run reads), event service (append meta-harness domain events) | Stateless task service replicas | No new tasks or status reads. Running tasks continue. The meta-harness run service retries task status updates and the code host retries code-host webhooks. |
| Meta-harness scheduler | Decide what runs next: priority, concurrency, spend caps, repo-area locks (section 10). | Repo-area locks (Redis keyspace) | None to other components. Admission decisions go to the event service as meta-harness domain events. | Task service (queued tasks), meta-harness run service (start), metering service (caller and global spend), pool manager (sandbox capacity), event service (append) | One logical meta-harness scheduler, leader-elected | No new admissions. Running tasks continue. Repo-area locks expire by TTL if the leader scheduler replica dies holding them. |
| Meta-harness run service | Run each task's state machine from admission to outcome (section 7), through the run service dispatcher activity, `route` activity and run activity. Run the meta-harness review loop on each task's pull request. The meta-harness run service is the only caller of the `ai-api-unified` router. | `run`, `routing_decision`, `config_version`, its Temporal namespace, code-host rate-limit buckets | start, cancel, kill switch, status, run and harness profile reads | ai-api-unified router (in-process), pool manager, harness server Run API, task service, event service, guardrails library | Run service Temporal workers | RunTask workflows pause and resume from their last step on restart. Per-run sandboxes keep running under their pool manager sandbox lease TTL. A retried run service run activity re-attaches to its pool manager sandbox lease and HTTP Gateway run grant by `run_id`. The retried run service run activity re-attaches to the harness server Run API event stream with `Last-Event-ID`. |
| Pool manager | Provide healthy per-run sandboxes and leave no orphaned sandbox running (section 8.4). | `sandbox`, `lease`, the pool manager artifact store | lease (by run id, with `harness_session_id`, returning the sandbox and its `session_state`), release, lookup by run id, sandbox capacity, sandbox health | Pool manager executor backend, harness server health and drain endpoints, event service, guardrails library | One logical pool manager, leader-elected | No new pool manager sandbox leases, so new runs wait. Running per-run sandboxes continue. Pool manager reconciliation enforces sandbox lease TTLs on recovery. |
| Event service | One validated, ordered record of every meta-harness domain event (section 13). The event service calls no other component. | `event`, the per-type event schema registry, event partitions, event retention, the event service commit notification | append(events), idempotent on `event_id` and validated per type; read(run_id, from_position); tail(filter, from_position) | Nothing | Stateless event service replicas over one Postgres schema | Event producers' event service client libraries buffer and retry, so runs continue. Event consumers lag, then catch up from their event cursor. |
| Metering service | Turn meta-harness domain events into cost, time and success per attempt and task (section 9). | Projection tables: `model_call`, `attempt_usage`, `check_result`, `task_outcome`, `spend` | Queries by caller, harness profile, model, tier and task type; caller and global spend | Event service (tail) | Stateless metering service workers, one event cursor per projection | Metering projections lag. Runs continue, and the HTTP Gateway still enforces each run's HTTP Gateway run grant spend limit. The meta-harness scheduler treats spend older than its staleness limit as at cap and holds new tasks for capped callers. |
| Harness analyzer | Propose ai-api-unified router tier and harness profile changes from metering service evidence. | `proposal` | List proposals, get proposal evidence, approve, reject | Metering service (queries), event service (read, append), code host (config PRs) | Harness analyzer batch jobs plus a small harness analyzer API | No new proposals. No effect on runs. |
| Operator console | Let operators see every task, run, meta-harness domain event and per-run sandbox. Send cancel, kill switch, operator review claims and verdicts, and harness analyzer proposal approval to the owning service (section 13). | Nothing | Operator console web UI | Event service (read, tail), task service, meta-harness run service, metering service, harness analyzer, pool manager, traces and metrics backends | Stateless operator console replicas | No operator visibility. Runs continue. |
| HTTP Gateway | `ai-api-unified-http`, the per-run sandbox's only model endpoint (section 6.4). | HTTP Gateway run grants, HTTP Gateway per-run spend counters (Redis keyspace) | Conversation turn, issue and revoke HTTP Gateway run grant | ai-api-unified library and its guardrail checks (in-process), model providers | Autoscaling HTTP Gateway replicas | HTTP Gateway conversation turns fail, and pi does not retry them. The run fails, and the RunTask workflow's run loop moves to the next candidate. When every tier candidate fails this way, the RunTask workflow pauses the task instead of failing it (section 17). |
| ai-api-unified router | `ai-api-unified`, in-process in the run service `route` activity. Classifies the task with the ai-api-unified router's classifier model, picks a tier, and returns the tier's candidates (sections 6.1 and 6.3). The RunTask workflow tries the candidates. | Nothing | `AiRouterCompletions` | Model providers (classifier model) | With the meta-harness run service | Fails with the meta-harness run service. |
| Harness server | One harness server per per-run sandbox. Exposes the harness server Run API and drives pi (sections 4 and 8.2). | The run's pi session directory, until pool manager drain | Harness server Run API | HTTP Gateway (through the pi model client extension), code host | One harness server per per-run sandbox, under the pool manager's sandbox capacity targets | The run ends in `ERROR`. The RunTask workflow's Decide step tries the next tier candidate or ends the task (section 6.3). |

**Guardrails.** The meta-harness run service and the pool manager call the
guardrails library for repo allowlists, credential scoping and caller and
global spend caps. The guardrails library owns no store. The HTTP Gateway runs
ai-api-unified's prompt and tool-result guardrail checks.

**Configuration.** The meta-harness run service loads `models.json`,
`harness_profiles.json` and `task_policy.json` and owns their version
history. An approved harness analyzer proposal becomes a pull request against
`config/`, reviewed and deployed like any configuration change. No meta-harness component
edits running configuration.

**Enforcement.** Each owner component has its own Postgres schema and database role, and
its own Redis keyspace. Postgres grants give no database role access to another owner component's schema.

## 4. The harness

The meta-harness harness is pi's coding agent (`@earendil-works/pi-coding-agent`),
which the harness server runs in-process through pi's SDK. pi's RPC
documentation recommends pi's SDK for in-process Node.js integration, and the
harness server is TypeScript in the same per-run sandbox. These owned pieces
make pi the meta-harness's agent and keep every pi model call inside
`ai-api-unified`:

- the **pi model client extension**, a pi extension loaded through pi's own
  extension mechanism. The pi model client extension registers the only model
  provider pi can see in the harness profile image and sends every pi turn to
  the HTTP Gateway;
- the **harness server** (section 8.2), which exposes the harness server Run
  API and runs pi's `AgentSession` through pi's SDK.

The ai-api-unified library never knows which agent a harness profile image
runs. Any harness profile image that implements the harness server Run API and
honors the run's pinned model can be the harness half of an ai-api-unified
router candidate, and a harness profile names the harness profile image.

### Model client

The pi model client extension registers one complete pi model provider with
`pi.registerProvider()` and its own `streamSimple`. The pi model client
extension needs its own stream function because the HTTP Gateway's
`/v1/conversations/turn` is not a protocol pi implements. The pi model
provider exposes a single pi model, "the run's model", which is whatever
model the HTTP Gateway run grant pins. The pi model has zero cost
metadata, because cost comes from the HTTP Gateway. It declares no
`promptCache` lifetime, so pi never warms the provider prompt cache (see
Prompt caching below).

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
   the pi model client extension emits text once per turn. With the HTTP
   Gateway streaming additions in section 6.5, the pi model client extension
   emits text model token by model token.
5. Finalizes pi's assistant message with the HTTP Gateway's numbers: input,
   output, cached input and cache-write model tokens, the per-turn cost, the
   stop reason, and the model the HTTP Gateway used. The pi model
   client extension adds `cached_input_tokens`, `cache_write_5m_tokens` and
   `cache_write_1h_tokens` from each HTTP Gateway turn's `usage` to the run's
   totals for the harness server Run API's `GET /usage`. The HTTP Gateway
   reports provider prompt cache writes by lifetime because they bill at
   different rates: 1.25 times the input rate for 5 minutes and 2 times for
   1 hour on Anthropic and Bedrock.

**Prompt caching.** Every HTTP Gateway conversation turn sends the whole pi
session history, so provider prompt caching sets most of a turn's cost: a
cached prefix bills at about a tenth of the input rate on Anthropic.

- **The cache hint on every turn.** pi passes `cacheRetention` and `sessionId`
  to the pi model provider on every call. The harness profile image sets
  `PI_CACHE_RETENTION=long`, and the pi model client extension maps `long` to
  the `prompt_cache` retention `extended` above: 1 hour on Anthropic and
  Bedrock, 24 hours on OpenAI. A run often waits more than 5 minutes between
  pi turns while tests or builds run, and the default provider prompt cache
  would expire in that gap. `harness_session_id` as the provider prompt cache
  key sends every turn of a pi session to the same OpenAI prompt cache.
- **No cache warming.** pi's `cacheWarming` setting is `off`, and the pi model
  client extension's pi model declares no `promptCache` lifetime. Otherwise pi
  would send billed provider prompt cache refresh calls through the HTTP
  Gateway.
- **A byte-identical prefix.** The pi model client extension keeps the system
  prompt and pi tool list byte-identical for the whole run: no timestamps, no
  run ids, and a fixed pi tool order. A pi session compaction rewrites the pi
  session history, so the pi turn after it writes a new provider prompt cache
  entry once.
- **HTTP Gateway fields.** The HTTP Gateway accepts `prompt_cache` on the
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

- **Resources.** A custom `ResourceLoader` supplies only the pi model client
  extension and the owned pi extension tools. Nothing loads from the cloned
  task repo's `.pi/` directory: no pi settings, pi extensions, pi skills or
  `SYSTEM.md`.
- **pi extensions left out.** pi SDK sessions do not load the pi CLI's
  built-in `codemode`, `tool_search` and MCP pi extensions, and the harness
  server never adds them. `codemode` can run classifier and image models with
  the pi session's credentials.
- **pi models.** A `ModelRuntime` with no built-in credentials, so the pi
  model client extension's pi model is the only one pi can call. The harness
  server never calls `setModel`, so pi never switches models and the HTTP
  Gateway run grant decides the real model.
- **pi tools.** pi's SDK `tools` option comes from the harness profile.
- **Context files.** pi loads `AGENTS.md` and `CLAUDE.md` from the pi working
  directory unless pi context loading is off, so the harness profile's
  `context_files` setting decides. The run service dispatcher activity puts
  the task repo's `AGENTS.md` in the first pi prompt (section 7), so turning
  pi context files on sends it twice.
- **pi settings.** `SettingsManager.inMemory(...)` holds these pi settings,
  and pi reads no settings file:

| Setting | Value | Reason |
|---|---|---|
| `compaction.reserveTokens`, `compaction.keepRecentTokens` | The harness profile's `loop.compaction` | See Loop policy |
| `retry.enabled` | `false` | pi's default model-call retry (3 retries, backoff up to 60 seconds) would stack on the HTTP Gateway's ai-api-unified fallback chain. A failed pi turn fails the run, and the RunTask workflow's Decide step moves to the next tier candidate (section 6.3). pi's context-overflow recovery (compact, then retry) stays on. |
| `cacheWarming` | `off` | See Prompt caching |

pi also has pi virtual models (`registerVirtualModel`), which route each pi
model request. This meta-harness design does not use pi virtual models,
because the HTTP Gateway run grant decides the model.

### Tools

| Tool | Source | Limits |
|---|---|---|
| `read`, `ls`, `find`, `grep` | pi built-in tool | Output capped by bytes; binary files refused |
| `write`, `edit` | pi built-in tool | The pi model client extension's tool-call hook refuses protected paths from the task repo policy |
| `bash` | pi built-in tool | Timeout and output cap per harness profile. The pi model client extension's tool-call hook refuses commands outside the harness profile's allowlist. No network beyond the per-run sandbox egress allowlist |
| `run_tests` | owned pi extension tool | Runs the acceptance checks named in the task; results emitted as `tests_run` harness server Run API events |
| `submit_review` | owned pi extension tool, meta-harness review harness profiles only | Ends the review run with verdict `clean` or `changes` and its findings, as a `review_submitted` harness server Run API event. The meta-harness run service posts them as a code-host PR review (section 7) |
| `create_ticket` | owned pi extension tool, planning harness profiles only | Records one ticket draft (repo, title, body, labels, `Depends on:` lines) as a `ticket_drafted` harness server Run API event. The meta-harness run service creates the issues after the run (section 11, Goals and modalities) |

pi opens no pull request. When a run finishes, the run service's Publish step
(section 6.3) commits pi's changes to the task's branch, pushes it and opens
one PR per task, or pushes to the PR branch in a meta-harness fix task.
Code-host branch protection blocks default branches. The PR description
holds the run's final summary, which the harness prompt asks to list the
assumptions the run made, because no person answers questions.

A harness profile's `tools` list may remove pi tools but not add arbitrary
ones.

### Loop policy

The pi model client extension and the harness server enforce the harness
profile's `loop` settings:

- **Turn and time limits.** The harness server counts pi turns from pi SDK
  events and sends `abort` to the pi session at `max_turns` or `max_minutes`.
- **Plan first.** The first pi prompt requires a plan before edits; the pi
  model client extension's tool-call hook refuses writes until pi has emitted
  a plan.
- **Test before PR.** With `test_before_pr`, the Publish step runs only when
  the `run_tests` pi extension tool has passed in the run's pi session.
- **pi session compaction.** pi starts pi session compaction when the pi
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

### Sessions and where context lives

A pi session is one conversation with pi, named by `harness_session_id`. The
meta-harness run service picks the `harness_session_id`, one per pull request,
when the task that opens the pull request starts. Every meta-harness fix task
on that pull request continues the same pi session conversation in a follow-up
run: a run that continues an existing pi session. A meta-harness review task
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
directory from its Cloud Storage bucket into a fresh per-run sandbox
(section 8.4). The harness server then switches pi to that pi session and
continues it, so the pi session conversation keeps its tool history on the
same model.

A pi session records its working directory and never changes it, and outside
pi's interactive mode pi exits with an error when that working directory is
missing. Every per-run sandbox therefore clones the task repo at the same
path, `/workspace`, so a restored pi session finds its working directory.

This meta-harness design uses neither pi's `@earendil-works/pi-durable`
package nor `@earendil-works/pi-env`. The pi-durable package has pi's
experimental label, with an API that changes without notice, and it is a
separate pi harness (`Harness.open(storage)`), not pi's coding agent. The
pi-env package runs pi-durable's tools on a remote machine.

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
  `review` rules bound the meta-harness review loop (section 6.2).
- A meta-harness review harness profile, such as `pi-review`, has no `edit`
  or `write` pi tool, and the run service never publishes a review run's
  workspace, so a review run cannot change the pull request.
- The meta-harness run service validates every harness profile at startup:
  the harness profile image exists, the harness prompt set exists, and every
  pi tool name is known.

## 5. Technology

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 for the meta-harness control plane; TypeScript inside the per-run sandbox image only | The ai-api-unified library is Python, and the run service `route` activity uses the ai-api-unified library in-process; pi and its pi extensions are TypeScript |
| Harness | pi's coding agent, run in-process through pi's SDK (`createAgentSession()`). The meta-harness adds an owned pi model client extension and a harness server that serves the harness server Run API | A maintained pi agent loop, pi tools, pi context compaction and pi session format. pi's extension points let the pi model client extension send every pi model call to the HTTP Gateway. pi recommends its SDK over its RPC mode for in-process Node.js integration. |
| API | FastAPI for the meta-harness service APIs | Matches the HTTP Gateway (ai-api-unified-http) |
| Durable workflows | Temporal Cloud, for the meta-harness run service workflows | Meta-harness runs last hours, wait on external events, retry with backoff, and survive restarts. Temporal timers, Temporal signals and per-run Temporal workflow history come built in. |
| Platform | Google Cloud: one GKE Autopilot cluster (section 15) | Per-run sandboxes need Kubernetes with gVisor, and running every meta-harness service on that same GKE cluster is simpler than splitting some onto Cloud Run. |
| Sandboxes | Per-run sandboxes: pods with the gVisor runtime class in a `sandbox` namespace, one per run | Isolation, autoscaling, egress control. Managed sandbox services implement the same pool manager executor backend interface. |
| System of record | Cloud SQL for PostgreSQL, one schema and role per owning meta-harness component | Each meta-harness service's own tables (section 12); the event service's append-only `event` table with PostgreSQL LISTEN/NOTIFY as the event service's internal commit notification |
| Locks, counters and rate limits | Memorystore for Redis, one key prefix per owning service | The meta-harness scheduler's repo-area locks. The HTTP Gateway run grants and HTTP Gateway per-run spend counters. The meta-harness run service's token-bucket rate limiters for code-host API calls |
| Telemetry | OpenTelemetry to Cloud Trace, Cloud Logging and Cloud Monitoring; meta-harness domain events in the event service | Every meta-harness run is one OpenTelemetry trace, from task to pull request; every meta-harness domain event includes its trace id |
| Objects, images, secrets | Cloud Storage (bucket per owning service), Artifact Registry, Secret Manager (mounted only into the owning service's pods) | Same one-owner rule as the Cloud SQL databases |

Cloud or local models are an ai-api-unified configuration matter: a local
model is an `openai-compatible` completions engine entry (the ai-api-unified
OpenAI-compatible provider client) in `models.json`, and it can be the model
half of any tier candidate (harness profile and model), served by vLLM on an
optional GPU node pool (section 15).

## 6. Routing

Routing happens at two levels. Level 1 runs once per task: the ai-api-unified
router in the meta-harness run service picks a tier and that tier's
candidates (each a harness profile and a model), and the meta-harness run
service's RunTask workflow tries them. Level 2 runs on every model call inside
a run, in the HTTP Gateway. The tier candidates and the harness profiles
change only through `models.json` and `harness_profiles.json`. The harness
analyzer proposes those changes as configuration pull requests (section 3,
harness analyzer).

Every sequence diagram in this document groups its participants in boxes by
owner: the meta-harness control plane, the ai-api-unified library running
inside the meta-harness run service process, the per-run sandbox (a
meta-harness harness profile image, one per run), the HTTP Gateway service
(ai-api-unified-http), and parties outside the system (callers, the code
host, model providers).

**Level 1: per task, in the run service.** The meta-harness run service's
`route` activity asks the ai-api-unified router in the run service for the
task's tier and tier candidates. The RunTask workflow then tries the tier
candidates in order (section 6.3). Here both `standard` tier candidates fail
for infrastructure reasons, so the RunTask workflow escalates to the
`complex` tier. Had either run ended on a limit, the RunTask workflow's
candidate loop would have ended there.

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

**Level 2: per model call, in the HTTP Gateway.** The HTTP Gateway run grant
pins the tier candidate's model. pi agent turns stay on that model. The pi
model client extension's compaction call sets `route: trivial`, which the
HTTP Gateway turns into a tier band so the compaction summary runs on a cheap
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

### 6.1 Task routing

The meta-harness run service dispatcher activity renders the task spec
(title, description, acceptance checks, repo, file hints, size signals) into
one prompt. The run service `route` activity sends that prompt through
`AiRouterCompletions` in classify-only mode. The ai-api-unified router in the
run service classifies it, picks a tier, and returns the tier's ordered tier
candidates. Each tier candidate names a harness profile and the model pi uses
in the per-run sandbox. The RunTask workflow runs them (section 6.3).

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
The ai-api-unified router splits a tier candidate string at its first colon,
so it reads `harness` as the tier candidate's provider name and the rest as
its model string, and returns both unchanged. The meta-harness run service
validates each harness tier candidate at startup (principle 4): the harness
profile exists in `harness_profiles.json` and names a harness profile image
the pool manager's executor backend can run, and the inner
`<provider>:<model>` is a model configured in the ai-api-unified library. The
ai-api-unified router's own startup model check probes a tier candidate's
provider client (its ai-api-unified completions engine), and ai-api-unified
has no `harness` provider client, so the meta-harness run service calls the
ai-api-unified router in classify-only mode, which skips that probe (section
6.5).

The ai-api-unified router's classifier model reads the latest user message,
so the meta-harness run service dispatcher activity puts the task spec in
that message and keeps instructions in the system prompt.

The ai-api-unified library loads `models.json` once per process and reads
settings only when it builds a provider client. So the run service `route`
activity builds one ai-api-unified router client per process and
configuration identity, and shares that router client across tasks. Sharing
is safe because the ai-api-unified library keeps per-call state in context
variables. The meta-harness's own configuration
(`harness_profiles.json`, `task_policy.json`, `callers.json`) follows the
same pattern: loaded and validated at startup, held in memory, reloaded on a
file change with the new version recorded in `config_version`.

The ai-api-unified router classifies each task, and the RunTask workflow acts on the result:

- **Classify** (the ai-api-unified router): with the configured
  ai-api-unified router classifier model; below `min_confidence`, use
  `default_tier`.
- **Try tier candidates in order** (the RunTask workflow): a run that ends
  for an infrastructure reason (no per-run sandbox within `ready_timeout`,
  rate limited or quota exhausted at the inner model, a lost harness server,
  or `model_error` from a provider outage) moves to the next tier candidate
  in the tier. A run that ends on a limit (`max_turns`, `max_minutes`,
  `spend_limit`) is final (section 6.3).
- **Escalate** (the RunTask workflow): when the RunTask workflow has exhausted the tier and
  `on_exhausted` is `escalate`, the RunTask workflow calls the run service
  `route` activity again with `route_floor` one tier up, within the task
  policy's limits. A task that the ai-api-unified router's classifier model
  called hard fails visibly rather than running on a weaker tier. The
  RunTask workflow records every run's cost, including a failed run's.
- **Pin** (the RunTask workflow): a meta-harness fix task, an exploration
  run, and a task submitted with a `pin` field skip the run service `route`
  activity and run one named harness-profile-and-model pair, such as
  `harness:pi-careful/claude:claude-opus-5-5`, with no classification and no
  escalation. A meta-harness fix task pins the pair that opened its pull
  request, so its follow-up run continues the pull request's pi session on
  the same model. A meta-harness review task tries the task policy's
  reviewer pairs in order instead (section 6.2). The pair need not appear in
  a tier. The meta-harness run
  service checks the pair's inner model against the ai-api-unified model
  catalog's allow and deny lists, so a denied pair fails before any pool
  manager lease. The RunTask workflow records `route_method: model_pin` and
  the pair.
- **Floors per call** (the run service `route` activity): every
  ai-api-unified router call sets its own `route_floor` and `route_ceiling`.
  The run service `route` activity leaves the ai-api-unified router client's
  client-wide follow-up setting unused, because that shared router client
  serves many concurrent tasks.

Caller hints use the ai-api-unified router's per-call options:

- `complexity: high` on a task passes `route_floor: "complex"`.
- `max_tier: standard` passes `route_ceiling: "standard"`.
- A retry after a failed attempt passes `route_floor` one tier above the
  failed attempt.

**What each step returns.** The RunTask workflow learns routing only from
the run service `route` activity's return value, and cost only from the run
service run activity's Collect step, never from ai-api-unified library
events.

| Where | Fields |
|---|---|
| Run service `route` activity result | From the ai-api-unified router: `route_tier`, `route_method`, `route_confidence`, `thinking`, the ordered tier candidates, and `classifier_usd` for the ai-api-unified router classifier model call |
| Run service run activity Collect result | `run_id`, `sandbox_id`, the harness profile, `end_reason`, `turns`, `pr_url`, `elapsed_ms`, the prompt cache hit rate, the run's model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens` and `cache_write_1h_tokens`) from the harness server Run API `GET /usage`, and the dollars the HTTP Gateway counted against the run's HTTP Gateway run grant |
| `preview_route()` | Before a run: tier, confidence and `usd_max`, used for the run's HTTP Gateway run grant spend limit (section 6.2) |

The RunTask workflow writes the run service `route` activity result as the
meta-harness domain event `task_routed`, and each run service run activity
Collect result as one `attempt_finished` (section 13). Each run's
`attempt_finished` holds its `run_id`, so it joins the pool manager's
lease record for that run.

### 6.2 Task policy

`config/task_policy.json` holds the task policy rules that are about the task
rather than its difficulty.

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

- **Floors by task type** become `route_floor` on the ai-api-unified router
  call.
- **Exploration.** The RunTask workflow pins a configured share of eligible
  tasks to a challenger harness-profile-and-model pair (see Pin in section
  6.1), so the harness analyzer keeps current data on every harness profile
  and model. Complex and security tasks never explore.
- **Budget.** A run's HTTP Gateway run grant spend limit is
  `preview_route()`'s `usd_max` for the tier times the harness profile's
  `budget_multiplier`, capped by the task's `max_usd`. A task's `max_usd`
  also caps the total of its meta-harness review and fix tasks.
- **Review.** `reviewers` lists the meta-harness review task's
  harness-profile-and-model pairs in order. The RunTask workflow skips any
  reviewer pair whose model wrote the pull request, so a different model
  always reviews. `max_rounds` caps the meta-harness fix tasks per pull
  request (section 7, Review loop). `operator_alert_minutes` sets when a
  review round blocked on an operator raises an alert (section 7, Operator
  review). `merge: false` leaves a clean pull request open
  instead of merging it; the repo policy can set it per repo.

### 6.3 The run loop

The meta-harness run service's Temporal workflow (the RunTask workflow) runs
a task's tier candidates, one run per tier candidate. The ai-api-unified
router in the run service picks the tier and lists its tier candidates. The
RunTask workflow does everything after that.

**Why the RunTask workflow, and not a completions engine in ai-api-unified.**
In ai-api-unified, a completions engine is a provider client class the
ai-api-unified library's factory builds for one provider, such as its Claude
client or its OpenAI client. A `harness` completions engine would make one
`asend_conversation` call run a whole coding run, and the ai-api-unified
router would try tier candidates through the ai-api-unified fallback chain as
it does for model calls. The design does not do this:

1. Temporal owns a run's durability, retries, cancel and per-attempt
   visibility. An ai-api-unified library call would duplicate each of them
   (re-attach by call id, the pool manager lease TTL as
   `request_timeout_seconds`, cancel through the run's terminal state), and
   the run's lifecycle would have two owners.
2. The ai-api-unified fallback chain serves model calls that cost cents
   and take seconds. For runs, each fallback is a new per-run sandbox and a
   new multi-dollar attempt, decided by ai-api-unified library code that
   cannot see the task's budget or its other attempts.
3. ai-api-unified would hold a pool manager client and a harness server Run
   API client, so the ai-api-unified library would depend on meta-harness
   services, and each harness server Run API change would need an
   ai-api-unified library release.

ai-api-unified therefore includes no `harness` completions engine, and
nothing in the ai-api-unified library depends on Temporal or on meta-harness
services. A library user outside the meta-harness who wants to run a coding
harness from ai-api-unified would need a separate `harness` completions
engine that runs one run against a harness server Run API URL. It would not
need Temporal. It is not part of this design, because no caller
needs it.

The `run_harness` activity (the run service run activity) in
`samples/temporal-run-task` implements the Grant, Start, Wait and Collect
steps below.

**The `route` activity.** The run service `route` activity sends the task
prompt through `AiRouterCompletions` with a classify-only call and returns
the route: the tier, `route_method`, `route_confidence`, and the tier's
ordered tier candidates, each a harness profile and an inner model. No
meta-harness code names a model or a harness profile (principle 1).

**The candidate loop.** For each tier candidate the RunTask workflow runs one
run service run activity, which performs the Identify through Collect steps
below. The RunTask workflow then takes the Decide step: from the run's
terminal state it tries the next tier candidate, escalates a tier under the
task policy (section 6.2), or ends. The RunTask workflow checks the budget
between tier candidates, because it holds the task's remaining spend, its
attempts so far and the task policy's limits.

| Step | What happens |
|---|---|
| Identify | `run_id` is the RunTask workflow id, the attempt number and the tier candidate index, the same on every retry of the run activity. A meta-harness review or fix task has its own RunTask workflow id, so its runs get their own `run_id`s. The sample uses `<workflow id>:run-<n>`, n counting the tier candidates the task has tried. |
| Lease | Asks the pool manager for an existing pool manager lease on `run_id` first, so a retried run activity re-attaches to a run in progress. Otherwise requests `lease(run_id, image, harness_session_id, resources)` for the harness profile's image. The pool manager response holds the per-run sandbox address, the pool manager lease TTL and the `session_state`: `new`, `kept` or `restored`. In a deployment without the pool manager, the run activity starts one per-run sandbox per run itself, with the Docker executor backend (section 8.3). |
| Grant | Asks the HTTP Gateway for an HTTP Gateway run grant (ai-api-unified-http 1.18.0), which the HTTP Gateway issues and stores. The run grant holds the run id, tags, the tier candidate's inner model as a pin, the spend limit, and an expiry at `max_minutes` plus grace. Only the run grant key goes to the per-run sandbox. A retried run activity asks again with the same terms and gets a new run grant key for the live run grant. |
| Start | When `session_state` is `new`, `POST /run` on the per-run sandbox's harness server Run API with the prompt, acceptance checks, harness profile, limits and run grant key. When it is `kept` or `restored`, `POST /followup` with the new input and its own run grant key. |
| Wait | Follows the harness server's event stream until `run_finished`, heartbeating the last harness server event id on every event. The run activity's heartbeat timeout catches a dead Temporal worker, and its start-to-close timeout, the pool manager lease TTL, is the hard upper bound. A RunTask workflow cancel reaches the run activity through its heartbeat, and the run activity sends `POST /cancel` to the harness server. |
| Collect | Reads the harness server's `/usage`, and a finished run's changes from `GET /bundle`. Then revokes the HTTP Gateway run grant and releases the pool manager lease. Returns the terminal state: status, end reason, turns, the files changed, the run's final summary, elapsed time and model token totals. The terminal state also holds the dollars the HTTP Gateway counted against the run grant. The RunTask workflow records one cost line per run from that figure. |
| Decide | In the RunTask workflow. A run that ended `FINISHED` ends the candidate loop. A run that ended `ERROR` for an infrastructure reason (no per-run sandbox, a lost harness server, `model_error` from a provider outage) tries the next tier candidate. A run that ended on a limit (`max_turns`, `max_minutes`, `spend_limit`) is final. Trying a more expensive tier candidate after a cheaper one spent its budget doubles the spend. So only the task policy's explicit retry-up rule or a human repeats a run that ended on a limit. |
| Publish | A run service activity, after Decide ends the loop on a finished run. Commits the run's changes to the branch `mh/<task>` and pushes it with the run service's code-host app token. Opens the pull request, labeled `ai:meta-harness`, with `Closes <ticket>` and the run's final summary. A retried Publish finds the branch and the pull request it made. A finished run that changed nothing fails the task. A fix task pushes to its PR's branch instead. The sample commits and pushes from the mounted workspace with `git` and `gh` on the host. |

Each tier candidate is one run and one per-run sandbox. The RunTask
workflow's Temporal history records every attempt, tier candidate and
decision. The RunTask workflow also writes each run's `attempt_finished`
meta-harness domain event, which the operator console and the harness
analyzer read from the event service (section 13).

The RunTask workflow never calls a model itself. The ai-api-unified router's
classifier model call happens in the run service `route` activity, through
the ai-api-unified library. The run service `route` activity needs
ai-api-unified 3.29.0 or later for `route_confidence`.

### 6.4 Model calls inside a run

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

A pinned call ignores route hints. So the HTTP Gateway handles the
`route: trivial` that the pi model client extension sets on compaction and
auxiliary calls. When a request body includes `route`, the HTTP Gateway runs
that one call as a tier band instead of a run grant pin, with no floor and
the pinned model's tier as the ceiling. The call runs on the named tier, and
an unknown route falls back to the pinned model. pi agent turns, which have
no `route`, stay pinned. Under a banded run grant model
policy the ai-api-unified router inside the HTTP Gateway honors the hint as
it is, within the band.

### 6.5 Required additions to ai-api-unified

| Where | Addition |
|---|---|
| ai-api-unified 3.29.0 | `route_confidence` on `AITurnResult` and `AIPromptResult`, which the run service `route` activity returns. |
| ai-api-unified 3.30.0 | A classify-only entry point on the ai-api-unified router: `AiRouterCompletions.classify_route`, and `AiRouteClassifier` from `AIFactory.get_route_classifier()`. It returns the route and the tier's ordered tier candidates. The classify-only entry point runs no completion and probes none of the tier candidates' provider clients (ai-api-unified completions engines). The meta-harness run service validates harness tier candidates itself (section 6.1). |
| ai-api-unified-http 1.18.0 | HTTP Gateway run grants, at `POST`, `GET` and `DELETE /v1/run-grants`. The run service run activity asks for an HTTP Gateway run grant, which the HTTP Gateway stores in Redis. The run service run activity revokes the HTTP Gateway run grant when the run ends. The run service run activity authenticates with an HTTP Gateway API key from its environment. A run grant holds the run id, tags, the pinned model, the spend limit and the expiry. Its run grant key is opaque. The run grant's model overrides any provider or model the request body names. The run grant's attribution replaces the caller's headers on HTTP Gateway cost events. |
| ai-api-unified-http 1.18.0 | An HTTP Gateway per-run spend counter in Redis, the run's total on every HTTP Gateway conversation response, and `402 run_grant_spend_limit` once the run has spent its limit. |
| ai-api-unified-http 1.19.0 | One tool-result shape for every model provider on the HTTP Gateway conversation endpoint: `{"type": "tool_result", "tool_call_id", "result", "is_error"}`. The HTTP Gateway builds the answering model provider's message with ai-api-unified's `build_tool_result_message`, and puts a turn's results in one message where the model provider needs that. |
| ai-api-unified-http | `route` accepted in the HTTP Gateway conversation request body. On a run grant that pins a model, a body `route` switches that one call from pin to tier band. The pinned model's tier is the tier band's ceiling. pi agent turns without `route` stay pinned. |
| ai-api-unified library, optional | A streaming form of `send_conversation` that yields typed events (text delta, tool-call start, arguments and end, usage, done). It routes through the ai-api-unified router and the ai-api-unified fallback chain and commits to one model at its first delta. The operator console's per-turn view works without it. With it, the operator console could show model text as each model token arrives. ai-api-unified PII redaction does not apply to these streams. |
| HTTP Gateway, optional | A server-sent-events form of the HTTP Gateway conversation endpoint, for the streaming form above. |

Out of scope for the ai-api-unified library: an
ai-api-unified event sink, ai-api-unified factory configuration injection,
harness profiles in `models.json` (they stay in `harness_profiles.json`), an
ai-api-unified library tool loop, and a cancel event on ai-api-unified
completions calls. A RunTask workflow cancel reaches the run service run
activity through its Temporal heartbeat, and the run activity calls the
harness server Run API `POST /cancel` (section 7).

## 7. Run lifecycle

One meta-harness task can produce several harness runs: the first attempt,
fallbacks to another candidate from the tier's candidate list that the
ai-api-unified router returns, and retries on a higher tier. One harness run
is one per-run sandbox. Once the task's PR is open, the task's RunTask
workflow runs the meta-harness review loop below, which tickets meta-harness
review and fix tasks, each with its own runs.

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

Each meta-harness task has one meta-harness run service Temporal workflow
(RunTask), and its Temporal workflow id is the task id. Every harness run of
the task happens inside that RunTask workflow: the first attempt, fallbacks
and retries one tier up. A meta-harness review or fix task is a task of its
own, with its own RunTask workflow. Temporal allows only one open
execution per Temporal workflow id. `start` keeps the default conflict policy,
Fail, so a second `start` for a running task fails with `Workflow execution
already started` and never creates a duplicate RunTask workflow run. `start`
also sets the reuse policy to Reject Duplicate, so a closed task's id never
starts again. The Temporal web UI and `temporal workflow show --workflow-id
<task id>` find any task's RunTask workflow from the task id that the task
service Task API `POST /v1/tasks` returned.

The RunTask workflow runs these steps:

1. **Admit.** The meta-harness scheduler grants a slot (section 10) and calls
   the meta-harness run service's `start`.
2. **Prepare.** The guardrails library resolves the target repo's repo policy
   and mints a read-only code-host access token scoped to the target repo, for
   the clone. The run service dispatcher activity builds the harness prompt
   from a versioned prompt template: task spec, acceptance checks, the target
   repo's `AGENTS.md`, and the instruction to finish with a summary for the
   pull request, which the Publish step opens with the label `ai:meta-harness`.
3. **Route and run.** The run service `route` activity sends the task through
   the ai-api-unified router (in the run service process) with the task's
   floors, ceilings and exploration pin, and returns the tier and its
   candidates. The RunTask workflow writes the `routing_decision` row, which
   the meta-harness run service owns, and appends `task_routed` through the
   event service client library (sections 6.1 and 13). For each candidate the
   RunTask workflow runs the run service run activity in section 6.3, which
   leases a pool manager sandbox, requests an HTTP Gateway run grant, sends
   `POST /run` to the harness server Run API, follows the harness server Run
   API event stream to `run_finished`, and collects the result. The RunTask
   workflow then decides. Each harness run appends one
   `attempt_finished` meta-harness domain event. A retried run service
   activity uses the same `run_id`, so it re-attaches to the pool manager
   sandbox lease, the HTTP Gateway run grant and the harness server run in
   progress.
4. **Watch.** The run service run activity is the only consumer of its
   sandbox's harness server Run API event stream. It writes the `run` row,
   appends the run's harness server Run API events through the event service
   client library in batches, heartbeats the RunTask workflow, and marks the
   harness run stalled when the harness run misses a heartbeat window.
   Code-host webhooks for the run's PR (checks completed, merged, closed)
   arrive at the task service, which signals the RunTask workflow.
5. **Review loop.** The RunTask workflow tickets meta-harness review and fix
   tasks on the PR until a review comes back clean, then merges the PR (see
   Review loop below).
6. **Close.** When the PR merges or closes, when the meta-harness review loop
   ends `ready` or `review_unresolved`, or when attempts and task budget are
   spent, the RunTask workflow appends the outcome meta-harness domain events
   and sets the task's terminal status through the task service, which
   reports it.
7. **Cancel.** The operator console sends cancel or kill switch (per run, per
   caller, global) to the meta-harness run service, which appends the audit
   meta-harness domain event with the operator as actor. The RunTask workflow
   cancels the run service run activity, which calls the harness server Run
   API `POST /cancel` on the per-run sandbox directly, revokes the HTTP
   Gateway run grant, and releases the sandbox lease
   with the pool manager, which destroys the per-run sandbox.

Every terminal state releases the pool manager sandbox lease. The pool
manager then collects run artifacts into its artifact store and destroys the
per-run sandbox, or keeps it for the same pi session's next follow-up run
(section 8.4).

Timeouts come from each harness profile (`max_minutes`), and the task policy
sets the hard ceiling.

### Review loop

No person has to review a meta-harness PR. The RunTask workflow of the task
that opened the PR owns the PR until it merges or closes, and runs this loop:

1. **Checks.** The RunTask workflow waits for the code-host checks webhook on
   the PR's head commit. A failed required check goes to step 3 with the
   check's output. Passing checks go to step 2.
2. **Review.** The RunTask workflow tickets a meta-harness review task through
   the task service, with `parent_task_id`, the PR URL and the head commit
   SHA. The review task pins the first reviewer pair in the task policy whose
   model did not write the PR (section 6.2), and starts a new pi session on a
   meta-harness review harness profile with no edit tools. The review run
   reads the PR diff, the parent task's spec and acceptance checks, and runs
   the tests. Its `submit_review` pi extension tool posts the findings as a
   code-host PR review and ends the run with verdict `clean` or `changes`,
   recorded with the reviewed head SHA in a `review_submitted` meta-harness
   domain event.
3. **Fix.** On `changes` or a failed check, the RunTask workflow tickets a
   meta-harness fix task through the task service, with `parent_task_id`,
   the PR branch, and the findings or check output as the task description.
   The fix task pins the pair that opened the PR and continues the PR's pi
   session in a follow-up run (`POST /followup`), which pushes commits to the
   PR branch. The loop returns to step 1.
4. **Merge.** On `clean`, the meta-harness run service merges the PR through
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
and a meta-harness alert rule fires (section 13). A PR closed on the code host
ends the loop and the task.

### Operator review

When a meta-harness review run starts, the meta-harness run service appends a
`review_started` meta-harness domain event. The operator console shows it as a
notification to every operator watching, with the PR link and an offer to
review the PR too. The offer needs no answer:

- **No answer.** The offer closes when the agent review run ends, and the
  review round decides on the agent's verdict alone.
- **Claim.** An operator who takes the offer sends a review claim from the
  operator console to the meta-harness run service. The run service records
  the claim in its `operator_review` table and appends
  `operator_review_claimed` with the operator as actor. The first claim wins,
  and the operator console tells later operators who holds it. The agent
  review run still finishes, and the operator console shows its findings to
  the operator.
- **Block.** A claimed review round waits for the operator. The review task's
  RunTask workflow waits on a Temporal signal, so the wait survives restarts.
  The agent review run's per-run sandbox is released when that run ends, and
  Hi counts run time only, so the wait adds no dollar-hours (section 9). The
  parent task keeps its repo-area lock while it waits.
- **Verdict.** The operator reads the PR on the code host and submits a
  verdict, `clean` or `changes`, with findings, from the operator console. The
  meta-harness run service posts the operator's findings as a code-host PR
  review that names the operator, and appends `operator_review_submitted`.
  The round's verdict is `changes` when the agent or the operator says
  `changes`, and the fix task gets both sets of findings.
- **Release.** The operator can release the claim from the operator console,
  and the round then decides on the agent's verdict. A meta-harness alert
  fires when a claimed round has waited longer than the task policy's
  `review.operator_alert_minutes`. Nothing releases a claim on its own.

### Sequences

In each diagram below, the meta-harness run service RunTask workflow runs the
run service run activity, whose steps lease the pool manager sandbox, request
the HTTP Gateway run grant, start and follow the harness server run, and
collect. The RunTask workflow then decides. The ai-api-unified router
in the run service process only classifies the task in the classify-only
`route` activity and returns the tier's candidates.

**1. Happy path**

```mermaid
sequenceDiagram
    autonumber
    box rgba(128,128,128,0.14) Outside the system
    participant C as Caller (Task API client)
    end
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant TS as Task service (Task API)
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
    SB->>SB: commit, push, open PR
    SB-->>A: run_finished (SSE)
    Note over A: Collect step
    A->>SB: GET /usage
    SB-->>A: usage and reported cost
    A->>GW: revoke HTTP Gateway run grant (run_id)
    A->>PM: release pool manager sandbox lease
    A-->>W: terminal state FINISHED, PR URL, end reason, cost
    W->>W: Decide: FINISHED ends the candidate loop
    W->>W: run row (owned by the run service)
    W->>EV: append attempt_finished
    Note over W,EV: the meta-harness review loop runs on the PR until a clean review, then merges it (sequence 5)
    W->>W: on PR merge or close: outcome
    W->>EV: append task_closed
    W->>TS: set status: closed
    Note over EV: the metering service tails these meta-harness domain events into task_outcome with dollar-hours
```

**2. Console attaches to a run in progress**

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

**3. Compaction or auxiliary call inside a run**

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

**4. Spend cap hit**

```mermaid
sequenceDiagram
    autonumber
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant W as Meta-harness run service: RunTask workflow
    participant A as Meta-harness run service: activities
    participant PM as Pool manager
    participant EV as Event service
    participant TS as Task service (Task API)
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

**5. Review loop on an open PR**

```mermaid
sequenceDiagram
    autonumber
    box rgba(128,128,128,0.14) Outside the system
    participant CH as Code host
    end
    box rgba(66,135,245,0.12) Meta-harness control plane
    participant TS as Task service (Task API)
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
    SR->>CH: submit_review: code-host PR review with findings
    SR-->>RV: run_finished, verdict changes, findings
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
    SF->>CH: push commits to the PR branch
    SF-->>FX: run_finished
    FX->>TS: set status: done
    TS->>W: signal RunTask workflow: fix task done
    Note over CH,W: the loop repeats from the next checks webhook, until a clean review on the PR head with passing checks
    W->>CH: merge the PR at the reviewed head SHA
    CH->>TS: code-host webhook: PR merged
    TS->>W: signal RunTask workflow: PR merged
    W->>EV: append task_closed
    W->>TS: set status: closed
```

**6. Operator cancel or kill switch**

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

## 8. Harness execution

### 8.1 Sandbox

One per-run sandbox per meta-harness run, as a pod with the gVisor runtime
class in the `sandbox` namespace (section 15).

- **One image**, the harness profile image, built from a common base: git,
  language toolchains, Node, pi, the pi model client extension and the
  **harness server** (section 8.2). Harness profiles select prompts, pi tools
  and limits on that harness profile image. A harness profile may name a
  different harness profile image when a toolchain or a different coding
  agent needs it.
- **Repo**: the task's code-host repository, cloned with the run's read-only
  code-host access token at `/workspace`, the same path in every per-run
  sandbox. The per-run sandbox never pushes (principle 9): the run service's
  Publish step reads the run's changes through the harness server Run API
  `GET /bundle` and pushes them. In the Docker executor backend the run
  service clones on the host into the mounted workspace and publishes from
  it, so no code-host token enters the container at all.
- **Model endpoint**: the HTTP Gateway URL, set in the harness profile image,
  and the HTTP Gateway run grant key, which arrives in the `POST /run` or
  `POST /followup` body. No model provider key exists in the per-run sandbox.
- **Egress** from the per-run sandbox limited to the code host, package
  registries and the HTTP Gateway.
- **Resources** from the harness profile; 2 vCPU and 4 GB by default.
- **Lifecycle**: owned by the pool manager (section 8.4). A per-run sandbox
  serves exactly one meta-harness run at a time. When the meta-harness run
  ends, the pool manager destroys the per-run sandbox, or keeps it for the
  same pi session's next follow-up run (`kept`, section 8.4).

### 8.2 Harness server and the Run API

The harness server is a small TypeScript service inside every per-run
sandbox. It exposes one authenticated HTTP API, the harness server Run API, to
the meta-harness run service run activity, and runs pi in-process through
pi's SDK (`@earendil-works/pi-coding-agent`). The harness server creates the
pi session with `createAgentSession()` and the pi session setup in section 4,
and uses `AgentSessionRuntime` for `newSession()`, `switchSession()` and
`fork()`. pi reports every pi session state change as a typed pi SDK event
through `session.subscribe()`, so the harness server parses nothing from
terminal output.

| Endpoint | Purpose | pi SDK calls |
|---|---|---|
| `GET /health` | Harness server readiness: pi model client extension registered, the workspace directory exists, HTTP Gateway reachable. A `warm` per-run sandbox passes it before it clones any task repo (section 8.4) | pi model runtime state |
| `POST /run` | Start a new pi session for the task service task. The body holds the prompt, acceptance checks, harness profile, limits and the HTTP Gateway run grant key | `newSession()`, `prompt()` |
| `POST /followup` | Continue the pi session the per-run sandbox holds, kept or restored by the pool manager before the pool manager sandbox lease returned. The body holds the new input and the HTTP Gateway run grant key, and no pi session reference. | `switchSession()` for a restored pi session, then `prompt()` |
| `GET /events` | Harness server Run API events as server-sent events, resumable with `Last-Event-ID` | `session.subscribe()`: `message_update`, `tool_execution_*`, pi compaction events, `agent_end`, `agent_settled` |
| `GET /status` | `CREATING`, `RUNNING`, `FINISHED`, `ERROR`, `CANCELED`, `EXPIRED`, plus the last harness server heartbeat event | pi session state |
| `POST /cancel` | Stop pi. The pi session file keeps every pi session entry written before the cancel, so a later follow-up run can continue the pi session. | `abort()`, which waits until the pi session is idle |
| `GET /usage` | Model token totals (input, output, `cached_input_tokens`, `cache_write_5m_tokens`, `cache_write_1h_tokens`), model call counts, and the HTTP Gateway's running total, as the pi model client extension recorded them. These totals serve reconciliation against the run's `model_call` meta-harness domain events | pi model client extension counters |
| `GET /metrics` | Harness server OpenTelemetry metrics: pi turns, pi tool calls, files changed, test runs, elapsed time | harness server counters |
| `GET /bundle` | The run's changes against the commit it cloned, as a git bundle, for the run service's Publish step. Planned for the pool manager's executor backends; the Docker executor backend reads the mounted workspace | none: git in the workspace |

The harness server waits with `waitForIdle()` and sends `steer()` or
`followUp()` when input arrives while pi is still working. The harness server
never calls `setModel`: the model comes from the run's HTTP Gateway run grant,
and the thinking level from the `POST /run` body. The pi coding agent gets
shell access only through pi's `bash` tool, under the harness profile's
limits.

### 8.3 Executor interface in the control plane

The pool manager executor interface is the pool manager's interface to
wherever per-run sandboxes run. The meta-harness run service run activity
does not go through it: the run activity calls the harness server Run API
(`POST /run`, `POST /followup`, `GET /events`, `GET /usage`, `POST /cancel`)
directly, over mutual TLS, at the pod address in its pool manager sandbox
lease, with that lease's per-lease harness server bearer token. Every pool
manager executor backend provides these operations:

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

Pool manager executor backend implementations: Kubernetes pods on GKE, Docker
(development), managed sandbox services, and a fake pool manager executor
backend that replays recorded harness server Run API event streams for tests.

### 8.4 Pool manager

The pool manager is the only meta-harness service that creates and destroys
per-run sandboxes. It is a single leader-elected service that owns the
`sandbox` and `lease` tables and the pool manager artifact store, with a
pool manager reconciliation loop against the pool manager executor backend's
actual inventory. Other meta-harness services reach pool manager sandbox
leases only through the pool manager interface.

**Sandbox states**

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

- `provisioning`: pod created, harness profile image pulling, harness server
  starting.
- `warm`: harness server `/health` green, no task repo, no credentials, no
  meta-harness run. Idle, ready for a pool manager sandbox lease.
- `leased`: bound to one meta-harness run. The pool manager injects the run's
  read-only code-host access token at pool manager sandbox lease time, and
  the per-run sandbox clones the task repo then, always at `/workspace`. The HTTP Gateway
  run grant key arrives later, in the `POST /run` or `POST /followup` body.
- `kept`: the meta-harness run has ended and the per-run sandbox waits for the
  same pi session's next follow-up, for up to `sticky_window`. The pool
  manager has saved the pi session directory, and the meta-harness run service
  run activity revoked the HTTP Gateway run grant when the meta-harness run
  ended. The task repo and pi session directory stay in place.
- `draining`: the meta-harness run has ended. The pool manager is collecting
  artifacts (pi session directory, harness server logs, harness server
  `/usage`).
- `destroyed`: pod deleted, pool manager `sandbox` record kept for audit.

**Leases**

- The meta-harness run service run activity requests `lease(run_id, image,
  harness_session_id, resources)`. The pool manager sandbox lease request is
  idempotent on `run_id`. The pool manager response holds the per-run sandbox
  address, the pool manager sandbox lease TTL and a `session_state`:
  - `kept`: the per-run sandbox kept for that pi session is still inside its
    warm-sticky window, so the pool manager hands it back.
  - `restored`: the pool manager's Cloud Storage artifact bucket holds a pi
    session directory for that `harness_session_id`, so the pool manager takes
    a warm per-run sandbox of that harness profile image (or provisions one)
    and copies the pi session directory in before returning. The per-run
    sandbox clones the task repo at `/workspace`, the working directory the
    pi session recorded, because pi exits with an error when that directory
    is missing.
  - `new`: neither exists, so the pool manager hands out a warm per-run
    sandbox of that harness profile image, or provisions one.
- A pool manager sandbox lease has a TTL of the harness profile's
  `max_minutes` plus a grace period. When the pool manager sandbox lease TTL
  expires, the pool manager destroys the per-run sandbox whether or not the
  meta-harness run service run activity released the pool manager sandbox lease. A meta-harness run service run
  activity that crashes therefore cannot leak a per-run sandbox.
- The pool manager sandbox lease is the only path to a per-run sandbox's
  address and credentials. The harness server Run API accepts a per-lease
  harness server bearer token, so a per-run sandbox cannot be addressed
  outside its pool manager sandbox lease.
- **Saving sessions.** When a meta-harness run ends, the pool manager copies
  its pi session directory to the pool manager's Cloud Storage artifact bucket
  under `harness_session_id` before it keeps or drains the per-run sandbox. A
  kept per-run sandbox that fails loses nothing.
- **Warm sticky.** When a meta-harness run finishes, the pool manager may keep
  its per-run sandbox (`kept`) for a warm-sticky window (`sticky_window`,
  default 10 minutes) keyed by `harness_session_id`. A follow-up for the same
  pi session inside the warm-sticky window gets that per-run sandbox back and
  skips the restore. Otherwise the pool manager drains the per-run sandbox and
  the follow-up restores from the pi session directory in a fresh per-run
  sandbox. The warm-sticky window never extends a pool manager sandbox lease
  TTL, and a sticky per-run sandbox is still single-pi-session: it is never
  leased to another meta-harness run.

**Warm pool**

- A target count of warm per-run sandboxes per harness profile image, set in
  the pool manager pool configuration (`warm: 4`) and adjusted by the pool
  manager from meta-harness scheduler queue depth: the number of admitted
  tasks waiting per harness profile image over the last few minutes.
- Warm per-run sandboxes hold no task repo and no secrets, so they are safe
  to keep idle.
- The pool manager destroys and replaces a warm per-run sandbox older than
  `warm_ttl` (default 2 hours), so no per-run sandbox outlives a routine
  harness profile image or node change.
- Per-harness-profile-image and global maximums cap total per-run sandboxes.
  The GKE node autoscaler follows the pool manager's demand.

**Health**

- Before a pool manager sandbox lease: the harness server `/health` must be
  green within `ready_timeout`, else the pool manager marks the per-run sandbox
  `unhealthy` and replaces it. The pool manager sandbox lease request moves to
  the next warm per-run sandbox.
- During a meta-harness run: the meta-harness run service run activity
  forwards missed harness server heartbeat events to the pool manager. Two
  missed harness server heartbeat windows mark the per-run sandbox
  `unhealthy`. The pool manager destroys it, and the RunTask workflow's Decide
  step decides whether another tier candidate runs (section 6.3). The pool
  manager never repairs a per-run sandbox in place.
- The pool manager probes idle warm per-run sandboxes on an interval and
  destroys any that fail.
- The pool manager reconciliation loop runs every 30 seconds: it lists the
  pool manager executor backend's actual per-run sandboxes, destroys any with
  no pool manager `sandbox` record or whose pool manager sandbox lease has
  expired, marks pool manager `sandbox` records whose pod has vanished, and
  provisions per-run sandboxes until each harness profile image is back at its
  warm target.

**Recycling**

- A per-run sandbox serves exactly one pi session: one meta-harness run, or
  that run and the follow-ups a warm-sticky window hands back to it. The task
  repo, the read-only code-host access token
  and HTTP Gateway run grant key, the pi session and any files the pi coding
  agent created are specific to that pi session, and a fresh per-run sandbox
  per pi session is the security boundary between pi sessions.
- Recycling therefore means: drain (collect artifacts to the pool manager
  artifact store, read harness server `/usage` for reconciliation), destroy
  the pod, and provision a fresh replacement per-run sandbox to meet the
  warm-pool target.
- **Image rollover.** When a harness profile changes its harness profile image
  version, the pool manager stops leasing the old harness profile image,
  destroys its warm per-run sandboxes, provisions the new harness profile
  image to the warm target, and lets leased per-run sandboxes on the old
  harness profile image finish their meta-harness runs.
- **Node rollover.** The pool manager honors the Kubernetes orchestrator's
  drain signals: it destroys warm per-run sandboxes on a draining node at
  once. Leased per-run sandboxes get until their pool manager sandbox lease
  TTL, after which the GKE node autoscaler releases the node.

**Metrics** (pool manager OpenTelemetry metrics, alertable): per-run sandboxes
by state and harness profile image, provisioning time, time-to-lease, warm hit
rate, harness server health failures by cause, pool manager sandbox lease TTL
expiries, orphaned per-run sandboxes destroyed by pool manager reconciliation,
and artifact collection failures.

## 9. Metering

```
dollar-hours per successfully completed task = Σi (Di × Hi) / Σi Ci
```

| Term | Source |
|---|---|
| Di, dollars for attempt i | The harness server Run API `model_call` events, which the metering service tails from the meta-harness event service. They hold the HTTP Gateway per-turn model token usage and cost (cached input model tokens included). They also hold the HTTP Gateway per-run spend counter's running total for the meta-harness run. Add `classifier_usd` from the run service `route` activity result and the meta-harness control plane services' own model calls for the meta-harness run |
| Hi, hours for attempt i | Active meta-harness run execution time from `RUNNING` to a terminal state, from the harness server Run API run status |
| Ci, success for the task | Unweighted share of the meta-harness task's checks that passed. The checks are code-host CI checks on the code-host PR, acceptance checks named in the Task API task, and code-host PR merge. Read from the meta-harness run service's `check_completed` and `task_closed` meta-harness domain events |

Because each harness server Run API `model_call` event holds the HTTP
Gateway's numbers, metering service cost attribution needs no log parsing and
no HTTP Gateway event output.

Each run's `attempt_finished` meta-harness domain event, which holds the
dollars the HTTP Gateway counted against the run's HTTP Gateway run grant,
and the harness server Run API `/usage` endpoint are cross-checks for Di.
Each `attempt_finished` holds the meta-harness run id, so the metering
service joins it to that meta-harness run's other meta-harness domain events
and pool manager lease record, counts nothing twice, and flags any gap over
5%. The metering service records the cost of every run, including each run of
a meta-harness task whose every tier candidate failed, because the RunTask
workflow writes one `attempt_finished` per run. The metering service keeps
the spend on failed runs per meta-harness task as that task's wasted spend.

A task's attempts include the runs of its meta-harness review and fix tasks.
The metering service rolls each review and fix task's dollars and hours up to
its parent task by `parent_task_id`, so dollar-hours per completed task counts
the whole review loop, and reports the review loop's share separately.

The metering service tails the meta-harness event service and owns its metering
projection tables: one `model_call` row per harness server Run API `model_call`
event, one `attempt_usage` row per meta-harness run, a `check_result` row per
task check, a `task_outcome` row when the meta-harness task closes, and `spend`
per Task API caller and period. Other meta-harness services query these through
the metering service interface, never the metering projection tables.

**Prompt cache hit rate.** The metering service reports the model provider
prompt cache hit rate, cached input model tokens divided by input model tokens,
per meta-harness run, harness profile and model, from each harness server Run
API `model_call` event's `cached_input_tokens` and `input_tokens`. The prompt
cache hit rate and model token totals in the run service run activity's
Collect result are the cross-check. The metering service keeps
`cache_write_5m_tokens` and `cache_write_1h_tokens` separate, because the two
model provider prompt cache write kinds bill at different rates. Keeping them
separate lets the metering service price prompt cache writes and check them
against each pi coding agent turn's HTTP Gateway cost. A low prompt cache hit
rate means something in the pi coding agent prompt prefix changes between pi
coding agent turns, which multiplies the cost of every later turn. The
metering service raises an alert for it (section 13).

### Spend controls

- **Per turn:** the HTTP Gateway rejects a pi coding agent turn once the
  HTTP Gateway run grant spend limit for the meta-harness run is spent.
- **Per run:** the harness server ends the meta-harness run `ERROR` with
  `spend_limit` when the HTTP Gateway refuses a turn with
  `402 run_grant_spend_limit`, and ends it `EXPIRED` at `max_minutes`. The
  pool manager lease TTL destroys the per-run sandbox if the harness server
  does not end the meta-harness run.
- **Per caller:** a configurable caller daily spend cap; the meta-harness
  scheduler reads the Task API caller's spend from the metering service, stops
  admitting that Task API caller's new meta-harness tasks at the caller daily
  spend cap and warns at 80%.
- **Global:** a hard daily global spend cap and a meta-harness kill switch.

## 10. Scheduling and scale

### Admission

The meta-harness scheduler runs its admission pass every few seconds and on
every task-related meta-harness domain event:

1. Order meta-harness tasks by task priority, then task age.
2. Admit a meta-harness task only if all hold: global meta-harness run
   concurrency below the global concurrency limit, the Task API caller below
   its caller concurrency quota and caller daily spend cap, pool manager
   per-run sandbox capacity available, and the target repo area not locked by
   another meta-harness run.
3. **Repo-area locks.** The meta-harness scheduler repo-area lock key is the
   repo plus the top-level directories the meta-harness task names. Unscoped
   meta-harness tasks lock the whole repo.
4. **Goals.** A goal's task is admitted only when every issue it depends on
   is closed, while the goal is active and its `max_usd` has money left, and
   within its modality's parallelism (section 11, Goals and modalities).

### Throughput

Each of these resources scales independently:

| Resource | Unit of scale | Limit |
|---|---|---|
| Per-run sandboxes | Warm-pool targets and maximums per harness profile image, set by the pool manager | Node autoscaler follows the pool manager's demand; a second sandbox pool on a managed sandbox service |
| HTTP Gateway | HTTP Gateway replicas | Stateless and autoscaling; the single path for all pi coding agent model token traffic, so the HTTP Gateway has its own SLO and canary deploy |
| Model providers | ai-api-unified fallback chains per model, inside the HTTP Gateway | ai-api-unified fallback and provider cooldowns across model providers |
| Meta-harness control plane | Stateless meta-harness service replicas and Temporal workers | Each meta-harness service holds its own state behind its service interface (section 3); one meta-harness run service run activity per active meta-harness run |
| Code host API | Meta-harness code-host app installation with code-host webhooks | No code-host polling; conditional code-host API requests |

Per meta-harness run, the meta-harness control plane's own load is one
ai-api-unified router classification, about two short ai-api-unified
completions, and the harness server Run API event stream. The harness server's
pi coding agent turns go directly from the per-run sandbox to the HTTP Gateway.

## 11. Task API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/tasks` | Submit a meta-harness task. Returns the meta-harness task id. Idempotent on `external_ref`. |
| GET | `/v1/tasks?external_ref={ref}` | The Task API caller's meta-harness task with that `external_ref`. Lets a Task API caller find a meta-harness task by the Task API caller's own id |
| GET | `/v1/tasks/{id}` | Meta-harness task state, current meta-harness run, meta-harness routing decision, code-host PR URL, task outcome |
| GET | `/v1/tasks/{id}/events` | Server-sent meta-harness domain events for the meta-harness task's runs |
| POST | `/v1/tasks/{id}/cancel` | Cancel the active meta-harness run and close the meta-harness task |
| GET | `/v1/runs/{id}` | Meta-harness run state, meta-harness candidate, model token usage, cost so far |
| POST | `/v1/webhooks/code-host` | Code-host PR, check and review webhook events for code-host PRs that meta-harness runs opened |
| GET | `/v1/profiles` | The active harness profiles, routing tier candidates and meta-harness task policy with their config versions |
| GET | `/health` | Task service liveness and configuration identity |

Task fields:

| Field | Required | Meaning |
|---|---|---|
| `external_ref` | yes | The Task API caller's own id; the task service deduplication key |
| `repo` | yes | Code-host repository URL |
| `task_type` | yes | `bug`, `feature`, `refactor`, `dependency`, `test`, `security`, `docs`, or `plan` for a planning task (Goals and modalities below). The meta-harness itself tickets `review` and `fix` tasks (section 7, Review loop); Task API callers cannot submit them |
| `parent_task_id` | no | Set only on meta-harness review and fix tasks: the task whose PR they review or fix |
| `priority` | yes | Integer; higher-priority meta-harness tasks run first |
| `title`, `description` | yes | The meta-harness task text; treated as untrusted |
| `acceptance_checks` | yes | Commands or code-host check names that define meta-harness task success |
| `paths` | no | Repository paths the meta-harness task touches; scopes the meta-harness scheduler repo-area lock |
| `complexity` | no | `low`, `medium`, `high`; becomes a `route_floor` routing hint |
| `max_tier` | no | A routing tier name; becomes `route_ceiling` |
| `max_usd` | no | A per-task spend cap; it caps each run's HTTP Gateway run grant spend limit (section 6.2) |
| `pin` | no | A meta-harness candidate such as `harness:pi-careful/claude:claude-opus-5-5`; the meta-harness task runs on exactly that harness profile and model with no ai-api-unified router classification or meta-harness escalation |
| `goal_id` | no | The goal the task belongs to. The task service sets it on the tasks it makes from a goal's issues |
| `depends_on` | no | `external_ref`s of tasks this one waits for; the meta-harness scheduler admits it once they are all closed |

Task API callers authenticate to the task service with a per-caller Task API
caller key. The Task API caller key sets the caller concurrency quota and the
caller daily spend cap for that Task API caller.

`external_ref` is unique per Task API caller. The task service's task table has
a unique index on `(caller, external_ref)`, and both the task service
deduplication and the `external_ref` lookup use it. One meta-harness task per
Task API request: to fix three bugs, a Task API caller submits three
meta-harness tasks and gets three meta-harness task ids.

### Goals and modalities

A goal is what the meta-harness works toward, and what the operator console
tracks. Its tasks come from GitHub issues across one or more registered
repos. Several goals run side by side, joint or independent, and compete for
run capacity by priority. A goal has:

| Field | Meaning |
|---|---|
| `modality` | `fix`, `feature` or `new-system`. Picks the goal's rules below |
| `repos` | The registered repos the goal may change (`config/repos.json`) |
| `label` | The GitHub issue label that queues an issue for this goal, in any of its repos |
| `statement` | What the goal is, in prose. The planning task reads it |
| `success_criteria` | Checks the task service evaluates to decide whether the goal is met: commands against a repo, an issue query such as "no open issues with the label", or a code-host check name |
| `latitude` | 0 to 1: how far the planner may go beyond what the goal names (Latitude, below). Defaults per modality |
| `max_usd`, `priority` | One budget and one priority for every task the goal queues, review and fix tasks included |
| `status` | `active`, `paused`, `met`, `stalled`, `budget_exhausted`, `canceled` |

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/goals` | Create a goal. A `feature` or `new-system` goal starts in `planning` and waits for approval |
| GET | `/v1/goals/{id}` | The goal, its issues across repos with each one's state and dependencies, its plan or phases, its criteria's live state, and its spend |
| POST | `/v1/goals/{id}/approve` | Approve the pending plan or phase |
| POST | `/v1/goals/{id}/pause`, `/resume`, `/cancel` | Steer the goal |

**The goal loop.** For every active goal the planner runs this loop, and the
modality decides each step:

1. **Take work.** Open issues with the goal's label in the goal's repos become
   tasks, one issue each, `external_ref` `github:<owner>/<repo>#<number>`.
   The planner refuses an issue in a repo the goal does not list and says why
   in a comment on the issue.
2. **Run.** The RunTask workflow routes and runs each task (sections 6 and 7),
   under the modality's routing floor and ceiling.
3. **Review and merge.** The review loop (section 7) reviews each pull request
   to the modality's bar and merges it, closing its issue.
4. **Evaluate.** When the goal's issues are all closed, or a phase ends, the
   planner evaluates the success criteria.
5. **Decide.** Criteria met: the goal is `met`. Not met and work remains: the
   loop continues. Not met and nothing is queued: a `fix` goal waits for
   issues, and a `feature` or `new-system` goal tickets a planning task to
   plan the next step.

**The modalities.** Each is a configuration profile in
`config/modalities.json` (principle 7), so changing how a modality behaves is
a configuration change.

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
  "modalities": {
    "fix":        {"intake": "queue", "plan": false, "approve": [], "latitude": 0,
                   "route_ceiling": "standard", "parallel": 8,
                   "merge_bar": ["regression_test", "small_diff", "no_api_change"]},
    "feature":    {"intake": "plan", "plan": true, "approve": ["plan"], "latitude": 0.25,
                   "route_floor": "standard", "parallel": "graph",
                   "merge_bar": ["acceptance_checks", "docs", "version_bump"],
                   "goal_check": "acceptance"},
    "new-system": {"intake": "phases", "plan": true, "approve": ["phase"], "latitude": 0.5,
                   "route_floor": {"design": "complex", "skeleton": "complex", "default": "standard"},
                   "parallel": {"skeleton": 1, "default": "graph"},
                   "merge_bar": ["design_conformance", "e2e_green", "phase_runnable"]}
  }
}
```

**From issue to task.**

| Task field | From the issue |
|---|---|
| `external_ref`, `repo` | `github:<owner>/<repo>#<number>` and its repo |
| `title`, `description` | The issue's title and body, treated as untrusted |
| `acceptance_checks` | The body's `## Acceptance checks` list, else the registry's checks for the repo |
| `task_type`, `complexity`, `max_tier` | The labels `type:<task type>`, `complexity:<low, medium or high>` and `max-tier:<tier>`, within the modality's floor and ceiling |
| `depends_on` | `Depends on: <issue>` lines in the body, plus the tracker's blocked-by links |
| `goal_id` | The goal |

The repo registry, `config/repos.json`, lists the repos the meta-harness may
change, each with its default branch, default acceptance checks and merge
policy. For this project it holds `davecthomas/ai-api-unified`,
`davecthomas/ai-api-unified-http` and `davecthomas/meta-harness`. The console
shows which of them each goal is working in now.

**Order.** A goal's task waits in `blocked` until every issue it depends on is
closed. Tasks with no open dependency run in parallel under the modality's
parallelism and the usual repo-area locks (section 10). The task service
refuses a dependency cycle and shows the issues in it. A task that waited on
another repo's change uses that repo's merged commit.

**Operator issues.** Publishing a release and deploying stay with operators
(section 1, Non-responsibilities). An issue labeled `meta-harness:operator`
belongs to the goal, and the meta-harness never runs it. The console lists it
under "needs you", and an operator closes it after doing the work, which
unblocks the issues that depend on it. So a goal can hold "publish
ai-api-unified 3.31.0" between the library change and the ai-api-unified-http
change that pins the new version:

```mermaid
flowchart LR
    A["ai-api-unified#120<br/>library change"] --> R["ai-api-unified#121<br/>publish 3.31.0<br/>meta-harness:operator"]
    R --> H["ai-api-unified-http#50<br/>pin 3.31.0"]
    H --> M["meta-harness#30<br/>call the new endpoint"]
    D["meta-harness#31<br/>docs for the new option"]
```

In this example the meta-harness runs ai-api-unified#120 and meta-harness#31
at once, because they depend on nothing open. ai-api-unified-http#50 waits
for an operator to close the release issue, and meta-harness#30 waits for
ai-api-unified-http#50 to merge.

**Closing.** Each task's pull request says `Closes <issue>`, so the merge
closes the issue. A task that ends without a merge (`exhausted` or
`review_unresolved`) leaves its issue open, with a comment holding the outcome
and the label `meta-harness:failed`, and the issues that depend on it stay
blocked.

**Joining later.** While a goal is active, the task service watches the goal's
repos for its label through the code-host app's issue webhooks. An issue
labeled after the goal started joins it, such as one the planning task wrote.

**Goal outcome.** A goal is `met` when its success criteria pass. It is
`stalled` when every open issue is blocked by a failed issue or waits on an
operator issue, and a meta-harness alert rule fires. Its `max_usd` covers
every task it queued, review and fix tasks included; once spent, the
meta-harness scheduler admits no more of the goal's tasks and the goal ends
`budget_exhausted`.

**Latitude.** A goal's `latitude` is a number from 0 to 1 that sets how much
scope the planner may add on its own, the way a sampling temperature sets how
far a model may stray from the likeliest token. It applies wherever the
planner decides what to do next: the planning task's prompt, the drafts the
planner accepts, and the Decide step.

| Latitude | The planner may | Example |
|---|---|---|
| 0 | Only the issues the goal names or people labeled. It drafts none, and it refuses a draft outside them. A `fix` goal at 0 is a pure queue | "Fix these twelve issues" |
| 0.25 | Draft the issues the statement needs and nothing beyond them. A draft must trace to a sentence in the statement | "Add the `route_ceiling` option" |
| 0.5 | Draft what the statement needs, plus the work it finds necessary on the way: tests the change needs, a doc the change makes wrong, a refactor the change needs to land | "Build the review loop" |
| 0.75 | Also draft improvements it finds in the goal's repos that serve the statement, as separate issues the operator sees in Inputs before they run | "Make the sample's end-to-end check faster and more thorough" |
| 1 | Keep going on its own: when the criteria are met it proposes the next goal from what it learned, and continues under the same budget until the budget or the operator stops it | "Improve the meta-harness" |

Above 0.5 every draft carries a `latitude_reason`, one line on why it serves
the statement, which the console shows beside the issue. Latitude never
loosens the modality's merge bar, the budget, the repo list or principle 9;
it widens only what the planner may propose. The modality sets the default:
`fix` 0, `feature` 0.25, `new-system` 0.5. The console shows the dial on the
Goal view, and an operator changes it while the goal runs.

**Planner.** The planner is the component that turns a goal into tasks and
decides when a goal needs more of them. It owns the goal loop above, the
`goal`, `plan` and `issue_link` tables, and the issue dependency graph. It is
the only meta-harness component that talks to an issue tracker, and it does so
through one interface, the issue tracker adapter:

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

**Bidirectional sync.** Issues flow both ways through the adapter. Inward, the
planner pulls work people wrote in the trackers: an issue someone labels in
GitHub, Jira or Linear joins its goal, and an edit to its body, labels or
dependencies updates the task before it runs. Outward, the planner writes what
it generated and what happened: the issues a plan drafted, each task's state
as a label or status (`meta-harness:running`, `meta-harness:pr-open`,
`meta-harness:failed`), the pull request link and the outcome as a comment,
and the close on merge. The `issue_link` row is the join: one row per issue,
holding the tracker's state and the meta-harness's, and the planner reconciles
the two on every webhook and on a periodic sweep that catches a missed one.
The tracker is the source of truth for what the work is; the meta-harness is
the source of truth for what happened to it. A conflict, such as an issue
closed by hand while its task runs, cancels the task and records why.

A `ref` is tracker-qualified: `github:<owner>/<repo>#<number>`,
`jira:<key>`, `linear:<identifier>`. Each registered repo in
`config/repos.json` names its tracker, and for Jira or Linear the project or
team and the field or label that names the repo an issue changes. The GitHub
adapter is first, over the code-host app. The Jira and Linear adapters follow
behind the same interface, and the planner, the console and the goal loop
change nothing for them.

**Planning task.** A `feature` or `new-system` goal's issues come from a
planning task, `task_type: plan`, which the planner tickets with the goal
statement, the modality and, for a later phase, what the earlier phases built.
The planning run reads the goal's repos and drafts issues with its
`create_ticket` pi extension tool: each in the repo it changes, labeled with
the goal's label, with acceptance checks and `Depends on:` lines. The run
writes nothing on any tracker (principle 9). After the run, the planner checks
each draft against the goal's repos and the modality's rules, refuses the
rest with the reason, and writes the plan to the `plan` table. The goal then
waits in `planning` until an operator approves the plan in the console, which
is the modality's checkpoint. On approval the planner creates the issues
through the adapter, which queues them. A `fix` goal never plans.

The planner also replans. A `new-system` goal's phase that ends with its
criteria unmet, or a task that fails and leaves dependents blocked, tickets a
planning task with the failure as input, and the plan it drafts goes through
the same approval.

The task service exposes the Task API over HTTP. The task service serves Task
API task routes from its own task table and answers Task API run, profile and
event routes by calling the meta-harness run service and the meta-harness event
service.

## 12. Data model

Each meta-harness table belongs to one owning meta-harness service (principle
8). The owning meta-harness service keeps it in its own Postgres schema under
its own Postgres role. No other Postgres role has a Postgres grant on it.

| Owner | Entity | Fields |
|---|---|---|
| Task service | task | id, caller, external_ref, repo, task_type, parent_task_id, goal_id, depends_on, pr_url, head_sha, priority, spec, acceptance_checks, created_at, status (including `blocked`, `ready` and `review_unresolved`) |
| Planner | goal | id, caller, modality, repos, label, statement, success_criteria, latitude, max_usd, priority, status (planning, active, paused, met, stalled, budget_exhausted, canceled), phase, created_at, closed_at |
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

The metering service `model_call` table holds one row per model call inside a
meta-harness run, so the harness analyzer can see which model answered each pi
coding agent turn; the meta-harness run service's run row records only the
meta-harness candidate the meta-harness run started on.

The meta-harness event service indexes its `event` table on `(run_id, position)`,
`(run_id, time_utc)` and `(event_type, time_utc)`, and partitions the `event` table by time and
retention class. The metering service projection tables are projections of the
`event` table, built from a metering service cursor, and can be rebuilt by
replaying the `event` table.

Meta-harness stores outside Postgres follow the same single-owner rule: the
meta-harness scheduler owns its repo-area locks, the meta-harness run service
owns its Temporal namespace and code-host API rate-limit buckets, the pool
manager owns the pool manager artifact store, and the HTTP Gateway owns the
HTTP Gateway run grants and HTTP Gateway per-run spend counters. Each Redis
keyspace has one owning service.

## 13. Observability

Meta-harness domain events go to one meta-harness event service. Every
meta-harness producer service appends to the event service and every consumer
reads from the event service. The operator console is one of those readers.
Operational logs, OpenTelemetry traces and OpenTelemetry metrics go to
OpenTelemetry. Every meta-harness domain event includes the OpenTelemetry trace
id, so meta-harness domain events and OpenTelemetry traces join on it. No one
has to log into a machine to observe the system.

### Event service

The meta-harness event service is the system of record for what happened in
every meta-harness task and every meta-harness run. The event service owns the
`event` table, the per-type schema registry, position assignment, partitions,
retention and the commit notification (section 12). The event service calls no
other meta-harness component. It holds the meta-harness's own domain events
only. The ai-api-unified library keeps its own library events, cost ledger and
logs.

| Operation | Contract |
|---|---|
| `append(events)` | Idempotent on `event_id`. The event service validates each meta-harness domain event against its event type's registered schema in the schema registry. The event service rejects an unknown event type or invalid payload back to the producing meta-harness service as a bug. The event service client library never retries a rejected append. |
| `read(run_id, from_position)` | A meta-harness run's meta-harness domain events in `position` order, paged |
| `tail(filter, from_position)` | A live stream of meta-harness domain events matching a filter (run, task, type, source), in `position` order, resumable from any position |

**Producers.** Only meta-harness services append, through the event service
client library.

| Producer | Events |
|---|---|
| Task service (Task API) | `task_created`, meta-harness task status changes, received code-host PR webhooks |
| Planner | `goal_created`, `latitude_changed`, `plan_drafted`, `plan_approved`, `issues_created`, `issue_joined`, `task_blocked`, `task_unblocked`, `criteria_evaluated`, `replan_ticketed` and `goal_closed`, and the tracker webhooks it received |
| Meta-harness scheduler | Admission decisions, with the reason the meta-harness scheduler holds a meta-harness task |
| Meta-harness run service: RunTask workflow, `route` and run activities | `task_routed` from the run service `route` activity result. One `attempt_finished` per run from the run service run activity Collect result (sections 6.1 and 6.3), joined to its pool manager sandbox lease by `run_id` |
| Meta-harness run service: run activity (Wait step) | The per-run sandbox's harness server Run API event stream: `turn_started`, `model_call` (with the HTTP Gateway's model-token usage and running total), `tool_call`, `tool_result`, `file_changed`, `tests_run`, `guardrail_blocked`, `compacted`, `turn_finished`, `run_finished`. The run service run activity stays the only consumer of each per-run sandbox's harness server Run API stream. Harness server Run API heartbeats are not stored. |
| Meta-harness run service: RunTask Temporal workflow | Run state transitions, `check_completed` from the PR signals the RunTask workflow receives, `review_submitted` from each meta-harness review task's run (verdict, findings, reviewed head SHA), `review_started`, `operator_review_claimed`, `operator_review_submitted` and `operator_review_released` with the operator as actor, `review_ticketed` and `fix_ticketed`, `pr_merged`, and `task_closed`. Operator actions the RunTask workflow receives (cancel, kill switch), as audit events with the actor |
| Pool manager | `lease_granted` (with `session_state`), `lease_released`, `session_saved`, `session_restored`, `sticky_handoff`, `sandbox_recycled`. The pool manager lease record names the per-run sandbox for each run. |
| Harness analyzer | `proposal_opened`, and operator approvals and rejections of harness analyzer proposals as audit events with the actor |

The operator console produces no meta-harness domain events (see Console
below).

**Write path.** The event service client library buffers meta-harness domain
events locally and retries. The outbox lives in the event service client
library, not in each meta-harness producer service's tables. Retries are safe
because `append` is idempotent on `event_id`. An event service outage never
blocks a meta-harness run.

**Live delivery.** After an event service append commits, the event service
sends a commit notification, Postgres `NOTIFY`, with the run id and the new
highest `position`. The commit notification contains no meta-harness domain
event data and is internal to the event service: its listeners are the event
service's own `tail` handlers, which fetch the new event rows and stream them
to their callers. A missed commit notification costs latency, not data, because
the next `tail` handler fetch catches up. The design uses `NOTIFY` because it
fires only on commit. If commit notification volume exceeds what the event
service's Postgres database can handle, Redis can send the commit notification
instead.

**Ordering.** Each meta-harness producer service sets `seq` within its own
event stream. The event service assigns `position` on insert. Concurrent
Postgres transactions can commit out of `position` order, so the event service
`tail` handler re-reads a short window behind each caller's cursor and
suppresses repeats of meta-harness domain events it has sent. Delivery is at least
once, and a consuming service can drop a repeat meta-harness domain event by
`event_id`.

**Consumers.**

| Consumer | Calls |
|---|---|
| Operator console | `read` for a meta-harness run's event history, then `tail` from the last position (section 7, sequence 2) |
| Metering service | `tail` with one cursor per metering projection, into `model_call`, `attempt_usage`, `check_result`, `task_outcome` and `spend` |
| Harness analyzer | `read` for routing, attempt and run meta-harness domain events over time |
| Meta-harness alert rules | `tail` for stalled runs, failed meta-harness tasks with every routing tier exhausted, runs ended on `spend_limit`, and usage reconciliation gaps |
| Audit and export jobs | `read` for operator and configuration meta-harness domain events, and bulk export by time range |

**Retention.** The event service partitions its `event` table by time and by
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

The operator console is a cloud web app, the control point of the
meta-harness. Operators sign in with GitHub; the first deployment allows one
account, and a team allowlist comes later. The console owns no store and holds
no database connection: it reads from the task service, the meta-harness run
service, the event service, the metering service and the pool manager through
their interfaces, and sends commands to the owning service, which records
each as an audit event with the operator as actor. The console never writes a
table.

The console is built for many parallel tasks pursuing several goals at once.
Every view drills down to the next, and every list filters by goal, modality,
repo, time, harness profile, model, routing tier, task type and state.

| View | Shows | Actions |
|---|---|---|
| Overview | Every active goal with its modality, progress against its success criteria, spend against budget and tasks in flight. A "needs you" list: plans and phases to approve, operator issues (releases, deploys) only an operator can close, unresolved reviews, alerts. Run capacity, HTTP Gateway health and the kill switch | Create goal, kill switch |
| Inputs | The queued GitHub issues across every registered repo, by repo and by goal: each one's state (queued, blocked and on what, running, PR open, in review, merged, failed), issues no goal has picked up, and which repos each goal is working in now | Add an issue to a goal, drop one |
| Goal | The goal's statement, modality and latitude dial, its issue dependency graph across repos, the plan or the phase timeline with each phase's criteria, every success criterion's live state, spend over time, and its tasks. Each planner-drafted issue shows its `latitude_reason` | Approve plan or phase, set latitude, pause, resume, cancel |
| Tasks | One task: its issue, every ai-api-unified router routing decision, every run including fallbacks, every check result, the diff, the pull request, each review round's verdict and findings and the fix task it ticketed, the merge, and cost per run | Cancel |
| Run | Live timeline of the run's events: each turn's model call (model, thinking, tokens, cost, latency), tool calls, file changes, test runs, compactions and guardrail blocks, the OpenTelemetry trace, and the per-run sandbox | Cancel |
| Live runs | Every run in flight across every goal: task, repo, model, turn, spend as it accrues, time since its last event | Cancel |
| Routing | Tier picked versus the candidate whose run finished, classifier confidence, floors and ceilings by modality, moves between candidates and escalations | |
| Pool | Per-run sandboxes by image and state, warm targets versus actual, lease latency, health failures | |
| Spend and performance | Cost by goal, modality, repo, model, harness profile and routing tier. Cost per merged pull request and time to merge, by modality. Dollar-hours per completed task over time. Cache hit rate | |
| Configuration | The active routing tiers, harness profiles, modalities, task policy and repo registry, their version history, and the harness analyzer's pending proposals | Approve or reject a proposal |
| Audit | Every operator action and configuration change | |

**Telling runs apart.** Tasks running at the same time differ by task id and
goal. A task's own runs never overlap: fallback runs and retry runs run one
after another. The Tasks view lists them by run id in order, with the task's
review and fix tasks under it.

**Reading events.** The console never connects to a per-run sandbox or to a
producer service. To open a run, it calls the event service `read` for the
run's history and then `tail` from the last position, so a run in progress
opens at any point with no gap, and a slow console falls behind without
slowing a producer. Tool calls, tool results, file changes, test runs,
guardrail blocks, compactions, model tokens and spend are live as they
happen, from the `model_call` events recorded off the harness server Run API.
Model text arrives once per turn; with the streaming additions in section
6.5, token by token.

**Sensitive output.** Event payloads contain repository content. The console
scrubs secrets from the events it reads. The HTTP Gateway's per-turn
redaction applies to what the model receives.

**First form.** The first console (State of the build) serves the Overview,
Inputs, Goal, Tasks, Run, Live runs and Spend views over the task service and
event service on Cloud SQL, with goals of the `fix` modality. The Routing,
Pool and Configuration views, review joining and proposals follow the
services they read from.

### Alerts

Meta-harness alert rules tail the event service and read OpenTelemetry metrics.
They fire on: a meta-harness run stalled past its harness server heartbeat
window, pool manager lease TTL expiries, warm sandbox pool below target for
longer than the sandbox provisioning time, HTTP Gateway error rate or latency
past its SLO, a meta-harness task failing with every routing tier exhausted,
a meta-harness task ending `review_unresolved`, a claimed review round
waiting on an operator past `review.operator_alert_minutes`, spend past 80%
of any cap, usage reconciliation gaps over 5%, and a harness
profile and model whose cache hit rate falls below its configured floor.

## 14. Security

- **Identity.** A code-host app installed on each repo in the repo registry.
  A per-run sandbox gets a read-only, repo-scoped code-host access token for
  its clone, minted per meta-harness run and expiring within an hour. Only
  the meta-harness run service writes to the code host, with its own
  code-host app token, never from a per-run sandbox (principle 9): it pushes
  branches, opens pull requests, posts reviews, creates tickets and merges.
- **Secrets.** Secret Manager mounts each secret only into its owner service's
  pods. The HTTP Gateway holds the model provider API keys for every model call
  a meta-harness run makes. The meta-harness run service holds one more: a
  classifier provider API key for the ai-api-unified router classifier model
  only. A provider project limited to that model issues it, with its own spend
  limit, so a leaked classifier provider API key exposes classification and
  nothing else.
  Per-run sandboxes hold a run-scoped HTTP Gateway run grant key and a
  read-only code-host access token, both short-lived. No secret appears in prompts, meta-harness
  task specs or logs.
- **Untrusted text.** Task descriptions, meta-harness review findings, CI logs and
  every tool result pass ai-api-unified's prompt-injection and tool-result
  guardrail checks at the HTTP Gateway, and
  the meta-harness task prompt template places them in a delimited data
  section.
- **Sandboxing.** Each per-run sandbox has gVisor, a default-deny
  NetworkPolicy, the Secure Web Proxy allowlist, no model provider API keys, no
  cluster credentials and no Google Cloud permissions. The harness server is
  the only process in the per-run sandbox that talks to the meta-harness
  control plane, over mutual TLS.
- **Audit.** Every meta-harness task launch, review and fix ticket, operator
  review claim and verdict, merge, cancel and configuration change is a meta-harness domain event in the event service,
  recorded by the meta-harness service that received it, with its actor.

## 15. Deployment

The meta-harness runs on Google Cloud, one GCP project per environment. Every
managed service has one owning meta-harness component (principle 8), and
Workload Identity enforces the same rule at the infrastructure layer. The
ai-api-unified library runs in-process in the meta-harness run service pods
and the ai-api-unified-http HTTP Gateway pods.

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

The full deployment above is the target. The first deployment, which gets the
meta-harness running on its own with the console as its control point, uses
managed services where they exist and one VM where the design's pool manager
is not built yet:

| Component | Runs on | Notes |
|---|---|---|
| Operator console | Cloud Run | GitHub sign-in, one allowed account to start |
| Task service, event service | Cloud Run, Cloud SQL for PostgreSQL | The goal loop, the Task API, code-host webhooks, the event table |
| HTTP Gateway | Cloud Run | ai-api-unified-http, already deployed |
| Temporal | Temporal Cloud | The run service namespace |
| Run service worker, egress proxy, per-run sandboxes | One Compute Engine VM with Docker | The Docker executor backend from `samples/temporal-run-task`, as a systemd service that restarts on failure and on reboot |
| Code host | A GitHub App on the registered repos | Issue and pull request webhooks keep the Inputs view live without polling; read-only clone tokens per run; the run service's app token for every code-host write (principle 9) |

The VM is the one piece GKE and the pool manager replace later, behind the
same run service interfaces. Everything an operator does goes through the
console; nothing requires a terminal or a coding-agent session.

### Compute

- **One GKE Autopilot cluster**, private and regional.
- **`control` namespace.** One Deployment per meta-harness component: the
  meta-harness task service, the meta-harness scheduler, the meta-harness run
  service Temporal workers, the pool manager, the event service, the metering
  service, the harness analyzer, the operator console and the
  ai-api-unified-http HTTP Gateway, each with a horizontal pod autoscaler. The
  meta-harness scheduler and the pool manager each run two replicas with
  leader election.
- **`sandbox` namespace.** One per-run sandbox pod per run, with the gVisor
  runtime class (GKE Sandbox). The pool manager creates and deletes these
  per-run sandbox pods through the Kubernetes API, and the pool manager's warm
  pool is pre-created, unleased per-run sandbox pods. A default-deny
  NetworkPolicy on the `sandbox` namespace allows ingress only from the
  meta-harness run service and the pool manager on the harness server Run API
  port. It allows egress only to the HTTP Gateway's internal load balancer and
  the Secure Web Proxy.
- **Harness server Run API addressing.** The meta-harness run service and the
  pool manager reach a per-run sandbox's harness server at the pod address in
  the pool manager's sandbox lease record, over mutual TLS. No load balancer
  sits in front of the per-run sandboxes, because each run talks to exactly
  one per-run sandbox pod.
- **Cloud Run.** Per-run sandboxes need GKE (section 5). The
  ai-api-unified-http HTTP Gateway is the one component that could move to
  Cloud Run later.
- **Optional local models.** A GPU node pool runs vLLM behind the HTTP Gateway,
  as an `openai-compatible` ai-api-unified completions engine in the
  ai-api-unified `models.json` configuration.

### Managed services

| Service | Owner and use | Fails how |
|---|---|---|
| Cloud SQL for PostgreSQL | One regional high-availability Cloud SQL instance on a private IP. One schema and database role per owning meta-harness component (section 12), with IAM database authentication per component. The Cloud SQL instance has the capacity for the meta-harness event service, the heaviest writer. | Failover to the Cloud SQL standby takes about a minute. Meta-harness services retry, and event service producer clients buffer. Per-run sandboxes never touch Cloud SQL, so runs in progress keep working. New task admissions by the meta-harness scheduler and new sandbox leases from the pool manager wait. |
| Temporal Cloud | The meta-harness run service's Temporal namespace, reached over Private Service Connect. RunTask workflow history, timers and signals. | RunTask workflows stop advancing, but per-run sandboxes keep running and finish their work. When Temporal Cloud returns, run service activities retry and re-attach to the harness server by `run_id` and `Last-Event-ID`. A run whose pool manager sandbox lease TTL expires during the outage is lost, and the meta-harness run service retries it as a new attempt. |
| Memorystore for Redis | Standard tier with a replica. One key prefix per owning component. The HTTP Gateway keeps HTTP Gateway run grants and HTTP Gateway per-run spend counters. The meta-harness scheduler keeps repo-area locks. The meta-harness run service keeps code-host API rate-limit token buckets. The event service keeps a commit notification fallback, unused while Postgres `NOTIFY` suffices. | Memorystore failover keeps the data. If Memorystore loses the HTTP Gateway's spend counters or run grants, the HTTP Gateway fails closed. The HTTP Gateway then rejects model turns for any run it cannot verify or meter. Those runs end, and the meta-harness run service retries them as new attempts. If Memorystore loses the meta-harness scheduler's repo-area locks, the meta-harness scheduler stops admitting tasks until it rebuilds the locks from the run service's active runs. |
| Cloud Storage | One bucket per owning component. The pool manager's Cloud Storage artifact bucket holds pi session directories under `harness_session_id` and per-run sandbox logs. The meta-harness run service's bucket holds run artifacts, and the event service's bucket holds event exports. | The pool manager's sandbox drain step retries artifact collection. A per-run sandbox stays in `draining` until the pool manager saves the per-run sandbox's artifacts to the pool manager's bucket or a limit passes. Follow-up tasks that need a saved pi session wait. |
| Artifact Registry | Every container image: the per-run sandbox harness image and one image per meta-harness control-plane component. | Pods cannot pull uncached images. Running meta-harness services and warm per-run sandboxes keep running, and image rollover pauses. |
| Secret Manager | Secret Manager mounts each secret only into its owning component's pods. Model provider API keys go to the HTTP Gateway, except the ai-api-unified router classifier's provider API key. The ai-api-unified router classifier's provider API key goes to the meta-harness run service. The meta-harness run service also receives an HTTP Gateway API key. The run service run activity uses that HTTP Gateway API key to request and revoke HTTP Gateway run grants. | Secrets load into pods at pod start, so a Secret Manager outage blocks new pods only. |
| Cloud NAT and Secure Web Proxy | All egress from the GKE cluster. The Secure Web Proxy enforces the per-run sandbox egress allowlist. | Fails closed: per-run sandbox egress stops, runs fail on code-host clone or push, and the meta-harness run service retries them. No bypass route exists. |

### Network and identity

- **VPC.** The GKE cluster is private in its own VPC, with Cloud NAT for
  egress.
- **Per-run sandbox egress.** The Secure Web Proxy allows the HTTP Gateway's
  internal load balancer address, the code host and package registries, and
  nothing else. A per-run sandbox never reaches a model provider. The
  `sandbox` namespace's default-deny NetworkPolicy enforces the same limit
  inside the GKE cluster.
- **HTTP Gateway.** An internal HTTPS load balancer fronts the
  ai-api-unified-http HTTP Gateway.
- **Operator console and Task API.** An external HTTPS load balancer with
  Identity-Aware Proxy fronts both the operator console and the task service
  Task API. Programmatic Task API callers present a Google service account
  identity to IAP and their per-caller Task API caller key to the Task API.
  Code-host webhooks cannot present IAP credentials, so the code-host webhook
  route uses its own load balancer backend without IAP, and the task service
  verifies the code host's webhook signature on every delivery.
- **Workload Identity.** Each meta-harness component has one Kubernetes
  service account bound to one Google service account. IAM grants follow
  component ownership: a component's Google service account reaches only its
  own Cloud SQL database role, Cloud Storage buckets and Secret Manager
  secrets. Only the pool manager's service account can create or delete pods
  in the `sandbox` namespace. Per-run sandbox pods run under a service account
  with no Google Cloud permissions.
- **Redis prefixes.** Memorystore has no per-prefix IAM, so each owning
  component's Redis client wrapper enforces that component's key prefix.
- **HTTP Gateway run grants** are application-level: a run grant key is not
  a Google Cloud credential. The HTTP Gateway API key that the run service run
  activity uses to request and revoke run grants reaches only the meta-harness
  run service pods, through Secret Manager (section 6.5).

### Observability

An OpenTelemetry collector DaemonSet sends traces to Cloud Trace and logs to
Cloud Logging. Cloud Monitoring holds the metrics that drive the horizontal pod
autoscalers and the alerts in section 13. The meta-harness event service
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

## 16. Repository layout

```
meta-harness/
  config/
    models.json                # ai-api-unified: tiers of harness x model candidates, classifier, providers
    models.dev.json            # overlay for local development
    harness_profiles.json      # image, prompts, tools, loop policy, limits per profile
    task_policy.json           # floors by task type, exploration, hard ceilings
    callers.json               # keys, quotas, caps, repo allowlists
    repos.json                 # the repo registry: default branch, acceptance checks, merge policy
    modalities.json            # fix, feature, new-system: intake, approvals, routing bounds, merge bar
    prompts/                   # versioned prompt sets
  images/
    harness-pi/                # Node, pi, the model client extension, the harness server
      extension/               # model client, tool-call hook, run_tests / submit_review / create_ticket tools
      server/                  # Run API over pi's SDK (in-process AgentSession)
  src/meta_harness/
    # one package per component; each owns its tables, migrations and interface
    task_service/              # task table, Task API (FastAPI), code-host webhooks
    planner/                   # goals, plans, the goal loop, issue dependency graph
      trackers/                # issue tracker adapters: github first, jira and linear later
    scheduler/                 # admission, repo-area locks
    run_service/               # RunTask workflow; dispatcher, route and run activities; run, routing_decision, config_version
    pool/                      # pool manager: sandbox and lease tables, warm pool, health, reconciliation, artifact store
      executors/               # pool manager executor backends: GKE pods, Docker (development), fake (recorded events)
    events/
      service/                 # event service: event table, schema registry, partitions, retention, commit notification
      client/                  # producer client: local buffer and outbox, retries, append / read / tail
      schemas/                 # one schema per event type
    metering/                  # projections from tail, query API
    analyzer/                  # proposal table, evidence, approve / reject, config PRs
    console/                   # operator console: cloud web app, no store; reads the services, sends goal, cancel, kill switch and approval commands
    guardrails/                # library: repo policy, credential scoping, text checks, spend caps
  deploy/
    terraform/                 # project, VPC, GKE Autopilot, Cloud SQL, Memorystore, buckets, IAM, proxy
    k8s/                       # control and sandbox namespaces, Deployments, HPAs, NetworkPolicies, runtime class
    compose/                   # local development: Postgres, Redis, Temporal dev server, services, egress proxy
  tests/
    contract/                  # Run API contract; the image against a recorded HTTP Gateway
  docs/
    technical-design.md
```

## 17. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The ai-api-unified-http HTTP Gateway is the single path for every pi coding agent model token | An HTTP Gateway outage stops all runs | HTTP Gateway pods autoscale, run multi-zone and deploy by canary. The pi coding agent's own retry is off (section 4), so a failed HTTP Gateway turn fails the run. The RunTask workflow's candidate loop then moves to the next tier candidate. When every tier candidate fails this way, the RunTask workflow pauses the task instead of failing it |
| A run outlives the run service Temporal worker that started it | A run service Temporal worker restart mid-run loses the run activity that calls the harness server | The run is a run service run activity. Pool manager sandbox leases, HTTP Gateway run grants and the harness server's `POST /run` are idempotent on run id. The retried run activity re-attaches to the harness server run and its harness server event stream from the last heartbeated event id. Shown live by the meta-harness sample's restart demo |
| Weak prompts or loop policy in a harness profile | Wasted model spend | Harness profiles are configuration; the harness analyzer measures each harness profile and model; routing exploration keeps the harness analyzer's data current |
| The pi coding agent's extension points prove insufficient | A pi model call bypasses the HTTP Gateway, or meta-harness policy cannot be enforced. | A harness image contract test drives one turn end to end. The contract test covers the HTTP Gateway turn, pi events, pi tool call, pi tool result, cost per turn, and pi model switching disabled. The pi SDK setup in section 4 prevents a leak of pi's own model catalog or pricing. The pi SDK setup also prevents a pi compaction that bypasses the pi model client extension. The contract test only confirms both. Fork pi only on one of the contract test failures listed below this table. A pi fork replaces pi's model layer with the pi model client extension and codes the harness server Run API into pi directly. Nothing above the harness image changes |
| pi coding agent releases change the pi SDK API or the pi provider interface | The harness image breaks | Pinned pi version per harness image; the harness image contract test runs on every harness image build |
| Meta-harness event service outage or event write backlog | Operator console, metering service and alerts lag | The event service producer client library buffers and retries, keyed on `event_id`, and never blocks a run; event consumers resume `tail` from their event service cursor |
| A meta-harness component reads or writes another owning component's table directly | Hidden coupling between components, schema changes break other meta-harness services | One Cloud SQL schema and database role per owning component with no cross-grants; the operator console holds no database credentials |
| A meta-harness review loop that never comes back clean | Model spend spikes, a PR left open | The task policy's `max_rounds` fix tasks per PR, the parent task's `max_usd` across the review loop, the HTTP Gateway run grant spend limit per run, Task API caller caps, the `review_unresolved` alert, meta-harness kill switch |
| The authoring model and the reviewing model share blind spots | Defects merge when no operator joins the review | A different model always reviews (section 6.2). An operator can join any review round from the operator console. The review run reruns the acceptance checks itself. The merge needs passing required checks on the reviewed head SHA. The harness analyzer tracks defects found after merge per reviewer pair |
| pi coding agents weakening tests to pass acceptance checks | False Ci, and a weakened test merges | Acceptance checks defined outside the pi coding agent's PR; protected test paths in the target repo; the meta-harness review task flags changed tests |
| Parallel pi coding agents in one repo | Merge conflicts | Meta-harness scheduler repo-area locks; a meta-harness fix task rebases a PR whose merge conflicts |
| The ai-api-unified router classifier routes hard tasks to a low model tier | Failed run attempts | The ai-api-unified router classifier rounds up when unsure; the meta-harness run service retries one model tier up; the harness analyzer tunes model tier descriptions |
| Prompt injection through task text, code-host comments or pi tool results | Unsafe pi coding agent actions | HTTP Gateway checks on prompts and pi tool results, scoped code-host access tokens, per-run sandboxing, code-host branch protection |

The harness image contract test justifies a pi fork when it shows any of:

- the pi provider interface cannot represent an ai-api-unified tool-call or
  finish-reason shape
- pi's own model catalog or pricing leaks into a pi session
- a pi compaction or auxiliary call bypasses the pi model client extension
- the pi model client extension cannot constrain the pi tool set and the
  harness profile loop policy

## 18. Open questions

1. Whether a managed sandbox service is worth adding as a second pool manager
   sandbox pool for burst capacity, judged on cost per run-hour and per-run
   sandbox start latency against GKE Autopilot.
2. Pinned versus banded per-run model policy: whether answering a run's easy
   pi model turns from a cheaper model tier changes the run's results. An A/B
   test between the two model policies on the same harness profile answers
   this.
