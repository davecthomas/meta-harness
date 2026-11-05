# Harness server (pi)

The harness server is the Run API from section 8.2 of the technical design. It
runs pi's coding agent in its own process through pi's SDK
(`@earendil-works/pi-coding-agent` 1.5.0). Callers start, watch, follow up
and cancel a run over HTTP. Every model call pi makes goes through the model
client to the HTTP Gateway.

It runs inside each per-run sandbox, a container of this directory's
`Dockerfile`, with the task's workspace mounted at `/workspace`. The
[Temporal sample](../../samples/temporal-run-task/) starts one container per
run. It also runs as a plain local Node process, for development and its
tests.

| Path | Role |
|---|---|
| `server/main.ts` | HTTP server: auth, routes, request checks, the SSE stream, SIGTERM |
| `server/harness.ts` | The pi session, the runs it serves, turn and time limits, mapping pi's events to Run API events |
| `server/event-log.ts` | The ordered event log behind `GET /events` |
| `extension/model-client.ts` | The model client: pi's only provider. Sends each turn to `POST /v1/conversations/turn` |
| `extension/gateway-messages.ts` | Converts pi's transcript into the gateway's message list |
| `Dockerfile` | The per-run sandbox image: Node 22.23.3, git, the server and the model client |
| `test/server.test.ts` | End-to-end tests against a fake gateway |

The model client and message conversion started as the
[pi model client sample](../../samples/pi-model-client/). This copy reads the
run's grant key from the run in progress on every turn, so a follow-up run
uses its own key. It sends no engine or model: the run grant behind the key
decides both on the HTTP Gateway. Tool results go in the HTTP Gateway's one
shape for every engine, `{type: "tool_result", tool_call_id, result,
is_error}`, and the HTTP Gateway builds the answering engine's own message.
Both need ai-api-unified-http 1.19.0 or later.

## Run it in a container

```bash
cd meta-harness/images/harness-pi
docker build -t harness-pi:dev .
docker run --rm -p 127.0.0.1:8090:8090 \
  -e HARNESS_SERVER_TOKEN=local-harness-token \
  -e GATEWAY_URL=http://host.docker.internal:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v "$(mktemp -d):/workspace" \
  harness-pi:dev
```

The image sets `HOST=0.0.0.0`, `PORT=8090`, `WORKSPACE_DIR=/workspace`,
`SESSION_DIR=/sessions` and `HARNESS_ALLOW_BASH=1`, and runs as the `node`
user. pi's tools reach the host only through the mounted workspace. The
container holds no model provider key. `host.docker.internal` is the Mac
running Docker Desktop, where the HTTP Gateway listens.

## Run it as a local process

You need nvm with the Node version in `.nvmrc` (22.19 or later), and an HTTP
Gateway to call.

```bash
cd meta-harness/images/harness-pi
npm ci
HARNESS_SERVER_TOKEN=local-harness-token GATEWAY_URL=http://localhost:8080 node server/main.ts
```

It runs under plain `node`, which strips the TypeScript types, with no build
step and no tsx.

| Variable | Default | Meaning |
|---|---|---|
| `HARNESS_SERVER_TOKEN` | required | Every request sends `Authorization: Bearer <token>` |
| `PORT`, `HOST` | `8090`, `127.0.0.1` | `PORT=0` picks a free port |
| `GATEWAY_URL` | `http://localhost:8080` | The HTTP Gateway |
| `WORKSPACE_DIR` | a new temporary directory | pi's working directory. `/workspace` in the sandbox |
| `SESSION_DIR` | a new temporary directory | Where pi writes the session file. It stays outside the workspace, so it never lands in the repo |
| `HARNESS_ALLOW_BASH` | off | `1` lets a profile name the `bash` tool |
| `HEARTBEAT_SECONDS` | `5` | How often a `heartbeat` event goes out while a run is active |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | unset | When set, the server's own calls to the HTTP Gateway go through the proxy (`server/proxy.ts`). In a per-run sandbox this is the egress proxy. Loading pi's SDK replaces Node's proxy-aware dispatcher, so the server installs one itself |

**Outside a sandbox, pi's tools act on your machine.** `read`, `write` and
`edit` take paths, and nothing yet confines them to the workspace. The
server refuses `bash` unless `HARNESS_ALLOW_BASH=1`. Run only prompts you
trust until the server runs in a sandbox.

## The Run API

| Method and path | Answers |
|---|---|
| `GET /health` | `200` when the model client is registered, the workspace exists and the gateway answers `/healthz`. `503` otherwise, with the failing check |
| `POST /run` | `202` and the run's state when it starts the run. `200` when a run with that `run_id` already exists: the caller re-attaches and pi does not start again. `409` when the server already holds a session for another run |
| `POST /followup` | Sends new input to the session this server holds, as a new run. `202` or `200` as for `/run`. `409` while a run is active or when there is no session |
| `GET /events` | Server-sent events. Each event's SSE id is its sequence number. A client that reconnects with `Last-Event-ID: <n>` (or `?after=<n>`) gets every event after `n`, then each new one |
| `GET /status` | The latest run's state, or `?run_id=` for a given run, plus `last_event_id`. `404` before any run |
| `POST /cancel` | Stops pi with `abort()` and answers once the run has ended `CANCELED` |
| `GET /usage` | The run's token totals, call counts and the gateway's dollar total. `?run_id=` for a given run |

A `POST /run` body:

```json
{
  "run_id": "run-task-1e306cda:run_harness",
  "harness_session_id": "run-task-1e306cda",
  "prompt": "Create a file named hello.txt ...",
  "acceptance_checks": ["test -f hello.txt"],
  "profile": {"name": "pi-sample", "tools": ["read", "write", "edit", "ls"], "max_turns": 20, "max_minutes": 10},
  "grant_key": "rg_..."
}
```

`grant_key` is the key of the run grant the caller got from the HTTP
Gateway's `POST /v1/run-grants`. The grant fixes the run's engine and model,
caps its spend, and expires; the caller revokes it when the run ends. A
`POST /followup` body carries `run_id`, `prompt` and its own `grant_key`.

`harness_session_id` becomes pi's session id and the gateway's prompt cache
key. The grant key never appears in run state, events or logs.

### Status and end reason

| `status` | `end_reason` | When |
|---|---|---|
| `CREATING` | | The session is being created |
| `RUNNING` | | pi is working |
| `FINISHED` | `completed` | pi ended its turn with no error |
| `ERROR` | `model_error` | A gateway call failed. pi's retry is off, so the run ends and the library's fallback chain is the only retry |
| `ERROR` | `max_turns` | A turn started past the profile's `max_turns` |
| `ERROR` | `spend_limit` | The gateway refused a turn because the run had spent its grant's limit (`402 run_grant_spend_limit`) |
| `ERROR` | `harness_error` | pi or the server threw |
| `ERROR` | `server_shutdown` | The server got SIGTERM or SIGINT during the run |
| `CANCELED` | `canceled` | `POST /cancel` |
| `EXPIRED` | `max_minutes` | The run passed the profile's `max_minutes` |

### Events

Each event has `id`, `type`, `run_id`, `time` and `data`.

| `type` | `data` |
|---|---|
| `turn_started` | `turn` |
| `model_call` | The served `engine` and `model`, `served_by_fallback`, `finish_reason`, `tool_calls`, input, output, cached and cache-write tokens, `usd`, the run's `run_spent_usd` and `run_spend_limit_usd` from its grant, `cache_hit`, `latency_ms`. A failed call has `error`, the gateway's `error_code` and `aborted` |
| `tool_call` | `tool_call_id`, `tool`, `args` |
| `tool_result` | `tool_call_id`, `tool`, `is_error`, `output`, `duration_ms` |
| `file_changed` | `path`, `tool`, after a `write` or `edit` that succeeded |
| `compacted` | `reason` |
| `turn_finished` | `turn`, `stop_reason`, the assistant `text`, the number of `tool_calls` |
| `review_submitted` | A review run's `verdict` (`clean` or `changes`), `summary` and `findings`, each a `path`, `line` and `body`, from `submit_review` |
| `heartbeat` | `turns`, `elapsed_ms` |
| `run_finished` | `status`, `end_reason`, `error`, `turns`, `elapsed_ms`, `usage`, and `review`: the verdict from `submit_review`, or null |

Text, arguments and tool output over 2,000 characters arrive as
`{truncated, chars, preview}`.

## What pi gets

- **One model.** A `ModelRuntime` with no stored credentials, no
  `models.json` and no network catalog, plus the model client. The server
  checks at startup that the model client's model is the only one pi can
  reach, and it drops provider key variables from its own environment first.
- **No extensions, skills, prompt templates, themes or context files.**
  pi keeps its own coding-agent system prompt.
- **Settings in memory.** pi's retry is off and cache warming is off.
- **The profile's tools,** from pi's built-ins: `read`, `write`, `edit`,
  `ls`, `find`, `grep`, and `bash` when allowed. A profile may also name the
  meta-harness's own `submit_review` (`extension/owned-tools.ts`). It ends a
  review run with a verdict, `clean` or `changes`, a summary and findings. It
  refuses `changes` with no finding, and it writes nothing on the code host:
  the meta-harness run service posts the review after the run.
- **The task text as given.** `prompt()` runs with `expandPromptTemplates: false`.

## Tests

```bash
npm test
```

The tests start the server against a fake gateway, so they call no model and
cost nothing. They cover:

- the bearer token
- a full run, with its typed events, the file it wrote and its usage totals,
  sending the grant key and no engine or model
- re-attaching with the same `run_id`, which starts no new model call
- a second session refused with `409`
- a follow-up in the same session, with its own grant key and the earlier turns
- `Last-Event-ID` resume
- cancel during a model call
- the turn limit
- the grant's spend limit, which ends the run `ERROR spend_limit`
- a failed gateway call
- `submit_review`: a `changes` verdict with no finding refused, then the
  verdict recorded, the run ended with no further model call, and the review in
  `run_finished` and `GET /status`
- request checks
- SIGTERM during a run, which exits within seconds

## Not built yet

Each of these is in the design and missing here:

- **The router's band.** A run grant only pins one model. The compaction
  call's `route: trivial` hint waits for the gateway to expose the router.
- **Restoring a saved session** into a fresh sandbox with `switchSession()`.
  `/followup` works only on the session this server still holds.
- **The owned tools** `run_tests`, `create_ticket` and `ask_for_followup`,
  the `tests_run` event, and the tool-call hook for protected paths, the
  `bash` allowlist, plan-first and test-before-PR. Pull requests are not a pi
  tool: the meta-harness run service opens them after the run.
- **The compaction handler** (`session_before_compact`) and the profile's
  compaction settings. pi compacts on its own defaults.
- **`guardrail_blocked`,** which needs the gateway to report blocked tool
  results.
- **Thinking level.** Fixed at `off`.
- **Input during a run** with `steer()` or `followUp()`.
- **`GET /metrics`,** the container image, and the repo clone at `/workspace`.
- **A size limit on the event log,** which lives in memory for the life of
  the server.
