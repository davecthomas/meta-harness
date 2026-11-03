# Temporal run task sample

This sample runs a coding task as a Temporal workflow, with pi running on the
[harness server](../../images/harness-pi/). A Python worker stands in for the
design's run service (section 7 of the technical design). The workflow
`RunTask` has two activities:

- `prepare` reads a versioned prompt template and returns the prompt.
- `run_harness` starts the run on the harness server with `POST /run`,
  follows `GET /events` until `run_finished`, and reads `GET /usage`. It
  heartbeats on every event and returns a typed result: status, turns,
  tokens, cost, the files pi changed, pi's last answer, the attempt number,
  and whether the attempt re-attached to a run already in progress.

pi runs in the harness server's process, apart from the worker, so a run
keeps going when the worker dies. The run id is the workflow id plus
`:run_harness`, the same on every attempt. A retried attempt sends the same
`POST /run`, gets the run in progress, and reads its events from the last
event id it heartbeated. It never starts pi a second time.

The sample also shows two failure paths:

- **Cancel.** Temporal workflow cancellation reaches the running activity
  through its heartbeat, and the activity sends `POST /cancel`, which stops pi
  with `abort()`.
- **Worker restart.** The worker is killed with SIGKILL in the middle of a
  run. A new worker picks up the workflow from its history, Temporal retries
  the activity after the heartbeat timeout, and the retry re-attaches to the
  run.

It pins `temporalio` 1.34.0 and `httpx` 0.28.1. The SDK behavior it relies on
was checked against that version's source: heartbeats and heartbeat details,
cancel delivery in the heartbeat reply, `ActivityCancellationType`,
`workflow.unsafe.imports_passed_through()`, and dataclass payloads in the
default data converter.

## How it maps to the design's run workflow

| Design step (section 7) | In this sample |
|---|---|
| 1. Admit | The starter calls `start_workflow`. There is no scheduler and no slot. |
| 2. Prepare | `prepare` reads `prompts/hello-v1.txt`. There are no guardrails, no code-host token and no `AGENTS.md`. |
| 3. Route and run | `run_harness` calls the harness server directly. There is no router, harness engine or sandbox lease. The starter names the engine and model. A retried attempt re-attaches by run id, as the design's harness engine does by call id. |
| 4. Watch | `run_harness` reads the harness server's event stream and prints it to the worker log. There is no event service, pool manager or stall detection beyond the heartbeat timeout. |
| 5. Follow up | Left out. The harness server has `POST /followup`, but no pull request, code-host webhooks or signals drive it here. |
| 6. Close | The workflow returns the result to the starter. There is no task service. |
| 7. Cancel | Temporal workflow cancellation. The activity sends `POST /cancel`. There is no audit event and no lease to release. |

## The call path

```mermaid
sequenceDiagram
    participant S as starter
    participant T as Temporal dev server
    participant W as worker
    participant H as harness server
    participant G as HTTP Gateway
    S->>T: start_workflow(RunTask, template, engine, model)
    T->>W: workflow task
    W->>T: schedule prepare
    T->>W: activity task: prepare
    W-->>T: prompt
    W->>T: schedule run_harness
    T->>W: activity task: run_harness
    W->>H: POST /run with run id, session id, prompt, profile, token
    H-->>W: 202 and the run's state
    W->>H: GET /events
    loop each model turn
        H->>G: POST /v1/conversations/turn
        G-->>H: text, tool calls, usage, usd_cost
        H-->>W: turn_started, model_call, tool_call, tool_result, turn_finished
    end
    Note over W,T: heartbeat with the last event id, on every event
    H-->>W: run_finished
    W->>H: GET /usage
    W-->>T: HarnessResult
    T-->>S: RunTaskResult
```

The engine, model and prompt travel in the workflow input, so they appear in
the workflow history. `GATEWAY_API_KEY` and `HARNESS_SERVER_TOKEN` stay in
the worker's environment and never enter the history.

## Files

| File | Role |
|---|---|
| `run_task/workflows.py` | `RunTask`: timeouts, retry policy and cancellation type for each activity |
| `run_task/activities.py` | `prepare` and `run_harness`: start or re-attach, the event stream, heartbeats, cancel |
| `run_task/shared.py` | Task queue name and the dataclass payloads |
| `run_task/worker.py` | Runs the workflow and activities. Stops on SIGINT or SIGTERM. |
| `run_task/starter.py` | Starts one workflow, prints its id and web UI link, waits, then prints the event history and the result |
| `prompts/hello-v1.txt` | The prompt template: create hello.txt and read it back |
| `Makefile` | `make run`, `make cancel-demo`, `make restart-demo` |

## Settings

| Setting | Value | Why |
|---|---|---|
| Run id | `<workflow id>:run_harness` | The same on every attempt, so a retry re-attaches |
| Harness session id | The workflow id | pi's session id and the gateway's prompt cache key |
| Profile | tools `read`, `write`, `edit`, `ls`, 20 turns, 10 minutes | The harness server enforces both limits |
| `run_harness` start-to-close timeout | 10 minutes | The whole run |
| `run_harness` heartbeat timeout | 10 seconds | A worker that stops heartbeating for this long is treated as gone, and the attempt is retried |
| Heartbeats | One per event, and the harness server sends a `heartbeat` event every 2 seconds. `max_heartbeat_throttle_interval` is 1 second on the worker. | A cancel reaches the activity only in a heartbeat reply. By default the SDK holds heartbeats back to 0.8 times the heartbeat timeout. |
| Retry policy | 3 attempts, 2 seconds first interval | Covers a lost worker and a lost connection to the harness server. A run that ended `ERROR`, `CANCELED` or `EXPIRED` fails the attempt for good, because a retry would re-attach to the same ended run. A refused request does too. |
| Cancellation type | `WAIT_CANCELLATION_COMPLETED` | The workflow ends only after the harness server has stopped pi |

## Run it

You need:

- the Temporal CLI: `brew install temporal`
- [uv](https://docs.astral.sh/uv/)
- nvm with the Node version in `../../images/harness-pi/.nvmrc`
- a checkout of `ai-api-unified-http` with provider keys in its `.env`

No Temporal account or API key is needed. The dev server runs locally.

```bash
cd meta-harness/samples/temporal-run-task
make run
```

`make run` takes these steps:

1. Runs `uv sync`, and `npm ci` in `images/harness-pi` when its
   `node_modules` is missing or older than its lockfile.
2. Starts `temporal server start-dev` unless a Temporal server answers at
   `TEMPORAL_ADDRESS`.
3. Starts the gateway unless one answers at `GATEWAY_URL`.
4. Starts a new harness server. A harness server holds one session, so
   `make run` stops if something already answers at `HARNESS_URL`.
5. Starts the worker, shows its log, and runs the starter.
6. Stops everything it started, on exit or Ctrl-C. On Ctrl-C the starter
   cancels the workflow and waits for it to end before the worker stops.

Logs go to `temporal.log`, `gateway.log`, `harness-server.log` and
`worker.log` in this directory. The dev server keeps workflow history in
memory, so history is gone once `make run` stops it. To keep history and
browse it later, set `TEMPORAL_DB` to a file path, or start your own dev
server first with `temporal server start-dev`. `make run` then uses that one
and leaves it running. The web UI is at http://localhost:8233 while the
server runs.

| Variable | Default |
|---|---|
| `SAMPLE_ENGINE`, `SAMPLE_MODEL` | `claude`, `claude-haiku-4-5` |
| `HARNESS_DIR` | `../../images/harness-pi` |
| `HARNESS_PORT`, `HARNESS_URL` | `8090`, `http://127.0.0.1:8090` |
| `HARNESS_SERVER_TOKEN` | `local-harness-token` |
| `GATEWAY_DIR` | `../../../aiapi/ai-api-unified-http`, the gateway checkout |
| `GATEWAY_PORT`, `GATEWAY_URL` | `8080`, `http://localhost:8080` |
| `GATEWAY_API_KEY` | `local-dev-key`, the gateway's local development key |
| `TEMPORAL_PORT`, `TEMPORAL_ADDRESS` | `7233`, `localhost:7233` |
| `TEMPORAL_DB` | empty: history in memory |
| `WAIT_SECONDS` | `60`, how long to wait for each service to start |
| `CANCEL_AT`, `RESTART_AT` | `turn 1 starts`, `turn 2 starts`: the worker log line each demo waits for |

```bash
make run SAMPLE_ENGINE=openai SAMPLE_MODEL=gpt-4o-mini
```

A run costs under a cent on `claude-haiku-4-5`.

## Reading the output

Each line starts with a tag that names where it came from.

| Tag | Source |
|---|---|
| `[make]` | The Makefile |
| `[worker]` | The worker process starting and stopping |
| `[workflow]` | `RunTask`, through `workflow.logger`: each step it schedules and what came back |
| `[activity]` | `prepare` and `run_harness`, through `activity.logger`: each attempt and its heartbeats |
| `[run_harness attempt N]` | The activity: what `POST /run` did, then one line per harness server event, numbered with the event's id. Heartbeat events are not printed. |
| `[history]` | The starter, after the workflow ends: every event the Temporal server recorded for the run |
| `[temporalio.…]` | The SDK's own messages, such as a failed activity attempt |
| `[starter]`, `[result]` | The starter |

The `[history]` lines are Temporal's own record. A new worker replays them to
rebuild the workflow. Three things about them:

- `workflow.logger` is silent while a worker replays, so a `[workflow]` line
  prints once, when its step first runs. A worker that picks the workflow up
  from history prints one line saying so.
- Heartbeats are not events. The `[activity]` lines count them.
- An activity that was retried has one `ActivityTaskStarted` event, for its
  last attempt, and that event carries the previous attempt's failure.
  Temporal writes it when the attempt ends.

## What a run looks like

From `make run` on 2026-11-03, with ai-api-unified-http 1.17.1 and
library 3.29.1. Paths are shortened, and `...` marks lines left out.

```text
[make] starting the Temporal dev server on port 7233, log: .../samples/temporal-run-task/temporal.log
[make] starting the gateway from .../aiapi/ai-api-unified-http, log: .../samples/temporal-run-task/gateway.log
[make] gateway: {"status":"ok","service_version":"1.17.1","api_version":"v1","library_version":"3.29.1"}
[make] starting the harness server from .../meta-harness/images/harness-pi, log: .../samples/temporal-run-task/harness-server.log
[make] harness server: {"ok":true,"checks":{"model_client_registered":true,"workspace_exists":true,"gateway_reachable":true},"session_id":null}
[worker] polling task queue run-task on localhost:7233
[starter] started workflow run-task-22430bc9  engine=claude  model=claude-haiku-4-5
[starter] web UI: http://localhost:8233/namespaces/default/workflows/run-task-22430bc9
[workflow] run-task-22430bc9: started, run id 01a1a2ae-37d8-7036-97cc-69d46dfd1c92, template hello-v1, engine claude, model claude-haiku-4-5
[workflow] run-task-22430bc9: scheduling activity prepare (timeout 30s, up to 3 attempts)
[activity] prepare attempt 1: read the prompt from prompts/hello-v1.txt
[workflow] run-task-22430bc9: prepare returned a 135-character prompt
[workflow] run-task-22430bc9: scheduling activity run_harness (timeout 10m, heartbeat timeout 10s, up to 3 attempts)
[activity] run_harness attempt 1 started for run-task-22430bc9, heartbeat timeout 10s
[run_harness attempt 1] POST /run started run run-task-22430bc9:run_harness
[run_harness attempt 1] #1 turn 1 starts
[run_harness attempt 1] #3 model call: claude:claude-haiku-4-5  finish=tool_use  tool_calls=write,read  tokens in=1923 out=183 cached=0  usd=0.002838
[run_harness attempt 1] #4 runs tool write {"path": "hello.txt", "content": "Hello! It's great to see you today!"}
[run_harness attempt 1] #5 runs tool read {"path": "hello.txt"}
[run_harness attempt 1] #6 tool read failed: ENOENT: no such file or directory, access '.../harness-workspace-pfw6s1/hello.txt'
[run_harness attempt 1] #7 tool write done
[run_harness attempt 1] #8 file changed: hello.txt
[run_harness attempt 1] #9 turn 1 finished
[run_harness attempt 1] #10 turn 2 starts
[run_harness attempt 1] #11 model call: claude:claude-haiku-4-5  finish=tool_use  tool_calls=read  tokens in=2238 out=61 cached=0  usd=0.002543
[run_harness attempt 1] #12 runs tool read {"path": "hello.txt"}
[run_harness attempt 1] #13 tool read done
[run_harness attempt 1] #14 turn 2 finished: Let me try reading it again:
[run_harness attempt 1] #15 turn 3 starts
[run_harness attempt 1] #17 model call: claude:claude-haiku-4-5  finish=complete  tool_calls=none  tokens in=2324 out=64 cached=0  usd=0.002644
[run_harness attempt 1] #18 turn 3 finished: Perfect! I've successfully created the file `hello.txt` and read it back. The file contains: **"Hello! It's great to see you today!"** The file was created with exactly one line containing a short fri
[run_harness attempt 1] #19 run finished: FINISHED (completed)
[activity] run_harness attempt 1: run finished, 20 heartbeats sent, returning the result to Temporal
[workflow] run-task-22430bc9: run_harness returned on attempt 1: 3 turns, usd 0.008025
[workflow] run-task-22430bc9: completed
[history] events the Temporal server recorded for run-task-22430bc9:
[history]   1  WorkflowExecutionStarted          task queue run-task
[history]   2  WorkflowTaskScheduled
[history]   3  WorkflowTaskStarted
[history]   4  WorkflowTaskCompleted
[history]   5  ActivityTaskScheduled             prepare
[history]   6  ActivityTaskStarted               prepare, attempt 1
[history]   7  ActivityTaskCompleted             prepare
[history]   8  WorkflowTaskScheduled
[history]   9  WorkflowTaskStarted
[history]  10  WorkflowTaskCompleted
[history]  11  ActivityTaskScheduled             run_harness
[history]  12  ActivityTaskStarted               run_harness, attempt 1
[history]  13  ActivityTaskCompleted             run_harness
[history]  14  WorkflowTaskScheduled
[history]  15  WorkflowTaskStarted
[history]  16  WorkflowTaskCompleted
[history]  17  WorkflowExecutionCompleted
[starter] workflow run-task-22430bc9 completed
[result] template=hello-v1  run=run-task-22430bc9:run_harness  status=FINISHED (completed)  attempt=1  reattached=False
[result] turns=3  failed calls=0  tokens in=6485 out=308  usd=0.008025
[result] files changed: hello.txt
[result] answer: Perfect! I've successfully created the file `hello.txt` and read it back. The file contains: **"Hello! It's great to see you today!"** The file was created with exactly one line containing a short friendly greeting, and I've confirmed it by reading it back.
[worker] shutting down
[temporalio.worker._worker] Beginning worker shutdown, will wait 0:00:00 before cancelling activities
[worker] stopped
[make] worker stopped
[make] harness server stopped
[make] gateway stopped
[make] Temporal dev server stopped
```

Event ids missing from the list, such as 2 and 16, are `heartbeat` events,
which the activity does not print.

Each `WorkflowTask` trio in the history is the worker running workflow code
until it next waits: once at the start, then once after each activity
returns. Each pass ends by sending the server its next commands: schedule
`prepare`, schedule `run_harness`, complete the workflow.

The failed `read` on turn 1 is pi running a turn's tool calls at the same
time (finding 6 of the pi sample).

## Cancel

```bash
make cancel-demo
```

This runs the same task. When the worker log shows `turn 1 starts`, it runs
`temporal workflow cancel --workflow-id <id>`, the way an operator would.
Output from 2026-11-03, after the same startup lines:

```text
[run_harness attempt 1] #1 turn 1 starts
[make] temporal workflow cancel --workflow-id run-task-3db53910
[run_harness attempt 1] cancellation received (cancel_requested), sending POST /cancel
[run_harness attempt 1] harness server stopped pi: run CANCELED
[workflow] run-task-3db53910: canceled, and run_harness has stopped pi
[starter] workflow run-task-3db53910 canceled
```

`harness-server.log` shows pi's side. `abort()` canceled the model call in
flight, and the run ended:

```text
[harness-server] #2 run-task-3db53910:run_harness model_call error: This operation was aborted
[harness-server] #3 run-task-3db53910:run_harness turn_finished {"turn":1,"stop_reason":"aborted","text":"","tool_calls":0}
[harness-server] #4 run-task-3db53910:run_harness run_finished CANCELED canceled
```

The `[history]` lines show the order, from event 11:

```text
[history]  11  ActivityTaskScheduled             run_harness
[history]  12  WorkflowExecutionCancelRequested
[history]  13  WorkflowTaskScheduled
[history]  14  WorkflowTaskStarted
[history]  15  WorkflowTaskCompleted
[history]  16  ActivityTaskCancelRequested       run_harness
[history]  17  ActivityTaskStarted               run_harness, attempt 1
[history]  18  ActivityTaskCanceled              run_harness
[history]  19  WorkflowTaskScheduled
[history]  20  WorkflowTaskStarted
[history]  21  WorkflowTaskCompleted
[history]  22  WorkflowExecutionCanceled
```

The cancel request reaches the workflow first (12). The workflow then asks
for the activity to be canceled (16). `ActivityTaskStarted` comes after the
cancel request because Temporal writes it when the attempt ends.

## Worker restart

```bash
make restart-demo
```

When the worker log shows `turn 2 starts`, the Makefile kills the worker's
process group with SIGKILL, then starts a new worker. The harness server is a
separate process and keeps running pi. To do it by hand, run `make run` with
a Temporal dev server you started yourself, and in another terminal run
`pkill -KILL -f run_task.worker`, then `uv run python -m run_task.worker` in
this directory with `HARNESS_URL`, `HARNESS_SERVER_TOKEN` and
`GATEWAY_API_KEY` set.

Output from 2026-11-03:

```text
[run_harness attempt 1] #10 turn 2 starts
[make] killing the worker with SIGKILL (process group 17341). The harness server keeps running pi.
[make] Temporal retries run_harness once 10s pass with no heartbeat, plus the 2s retry interval
[make] starting a new worker
[worker] polling task queue run-task on localhost:7233
[activity] run_harness attempt 2 started for run-task-9ef411d2, heartbeat timeout 10s
[run_harness attempt 2] POST /run: run run-task-9ef411d2:run_harness already exists (FINISHED). Re-attaching after event 5, without starting pi again
[run_harness attempt 2] #6 tool read failed: ENOENT: no such file or directory, access '.../harness-workspace-duHHuD/hello.txt'
[run_harness attempt 2] #7 tool write done
[run_harness attempt 2] #8 file changed: hello.txt
[run_harness attempt 2] #9 turn 1 finished
[run_harness attempt 2] #10 turn 2 starts
[run_harness attempt 2] #12 model call: claude:claude-haiku-4-5  finish=tool_use  tool_calls=read  tokens in=2235 out=61 cached=0  usd=0.00254
...
[run_harness attempt 2] #19 run finished: FINISHED (completed)
[activity] run_harness attempt 2: run finished, 15 heartbeats sent, returning the result to Temporal
[workflow] run-task-9ef411d2: this worker has no state for the workflow, so it replays the history to rebuild it. Steps already in the history do not run again.
[workflow] run-task-9ef411d2: run_harness returned on attempt 2: 3 turns, usd 0.007924
[workflow] run-task-9ef411d2: completed
...
[history]  11  ActivityTaskScheduled             run_harness
[history]  12  ActivityTaskStarted               run_harness, attempt 2, previous attempt: activity Heartbeat timeout
[history]  13  ActivityTaskCompleted             run_harness
...
[starter] workflow run-task-9ef411d2 completed
[result] template=hello-v1  run=run-task-9ef411d2:run_harness  status=FINISHED (completed)  attempt=2  reattached=True
[result] turns=3  failed calls=0  tokens in=6484 out=288  usd=0.007924
[result] files changed: hello.txt
```

pi ran once. It finished all three turns while Temporal waited out the
heartbeat timeout, so attempt 2 found the run `FINISHED`. Had pi still been
working, attempt 2 would have followed the rest of the run live. The result
is one run's cost, $0.0079. Before the harness server, attempt 2 started pi
again and paid for the turns attempt 1 had finished.

Attempt 2 printed events 6 to 10 again. Its first heartbeat detail came from
attempt 1, whose last heartbeat Temporal recorded at event 5. Heartbeats are
throttled, so the recorded event id can trail the last event printed. The
activity reads events at least once, never fewer, and its file list ignores
repeats.

The new worker also replayed the workflow's history to rebuild it. The replay
matched `prepare` and the `run_harness` schedule to events 5 to 11 and ran
neither again, so `prepare` ran once.

## Findings

1. **Resolved: under tsx, pi ignored SIGTERM.** The first version of this
   sample ran pi under `npm start` and tsx. Importing pi's SDK adds a SIGTERM
   listener from `signal-exit` 3.0.7, through pi's dependency
   `proper-lockfile`, and tsx adds a hidden one in its child process. With
   both, pi kept running after SIGTERM, and the activity had to send SIGKILL.
   The harness server runs under plain `node` and handles SIGTERM itself: it
   aborts the run, ends it `ERROR` with `server_shutdown`, and exits. Its
   tests check that it exits within 5 seconds during a model call. Cancel
   uses `POST /cancel` and `abort()`, with no signal at all.
2. **A cancel does not stop a model call the gateway is already serving.**
   `abort()` cancels the model client's request, as `harness-server.log`
   shows in the cancel demo. The gateway does not stop a provider call when
   its client goes away. In an earlier cancel test the gateway finished the
   turn and billed it ($0.0031).
3. **Resolved: a retried attempt started the run again.** A subprocess could
   not be re-attached once its parent died, so attempt 2 started pi from the
   beginning and the finished turns were paid for and lost. Now the run lives
   on the harness server, `POST /run` is idempotent on run id, and attempt 2
   re-attaches.
4. **A worker shutdown during a run fails the activity attempt.** The
   worker's default `graceful_shutdown_timeout` is zero, so a running
   activity fails with "Worker is shutting down and this activity did not
   complete in time". The activity tells this apart from a workflow cancel:
   it leaves the run going on the harness server, and the next attempt
   re-attaches. If a cancel was already requested, the workflow ends Failed
   instead of Canceled, and it stays Running until some worker comes back to
   record that. The starter waits for the workflow to end before it exits, so
   Ctrl-C does not hit this. The run service needs a drain step for deploys.
5. **Resolved: the pi sample exited 0 when every model turn failed.** The
   harness server ends that run `ERROR` with `model_error`, and its tests
   cover it.
6. **Workflow history holds the prompt and the result in plain text.** Both
   show in the web UI. Real prompts carry repo content. The run service
   should decide what goes into workflow input and results, and whether to
   add a payload codec that encrypts them.
7. **A run that ended in error cannot be retried on the same harness
   server.** A retry with the same run id re-attaches to the ended run, and
   one server holds one session, so a new run id is refused. The activity
   therefore fails the attempt for good when the run ends `ERROR`, `CANCELED`
   or `EXPIRED`. In the design, a failed run is the router's fallback to the
   next candidate in a new sandbox. That needs the harness engine and the
   pool manager.
