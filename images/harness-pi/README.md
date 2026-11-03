# Harness server (pi)

The harness server is the Run API from section 8.2 of the technical design. It
runs pi's coding agent in its own process through pi's SDK
(`@earendil-works/pi-coding-agent` 1.5.0). Callers start, watch, follow up
and cancel a run over HTTP. Every model call pi makes goes through the model
client to the HTTP Gateway.

In the full design this server runs inside each sandbox, with the repo
cloned at `/workspace`. This first version runs as a local Node process. The
container image comes with the pool manager.

| Path | Role |
|---|---|
| `server/main.ts` | HTTP server: auth, routes, request checks, the SSE stream, SIGTERM |
| `server/harness.ts` | The pi session, the runs it serves, turn and time limits, mapping pi's events to Run API events |
| `server/event-log.ts` | The ordered event log behind `GET /events` |
| `extension/model-client.ts` | The model client: pi's only provider. Sends each turn to `POST /v1/conversations/turn` |
| `extension/gateway-messages.ts` | Converts pi's transcript into the gateway's message list |
| `test/server.test.ts` | End-to-end tests against a fake gateway |

The model client and message conversion started as the
[pi model client sample](../../samples/pi-model-client/). This copy reads the
token, engine and model from the run in progress on every turn, so a
follow-up run uses its own token.

## Run it

You need nvm with the Node version in `.nvmrc` (22.19 or later), and an HTTP
Gateway to call.

```bash
cd meta-harness/images/harness-pi
npm ci
HARNESS_SERVER_TOKEN=local-harness-token GATEWAY_URL=http://localhost:8080 node server/main.ts
```

It runs under plain `node`, which strips the TypeScript types, with no build
step and no tsx. The [Temporal sample](../../samples/temporal-run-task/)
starts it for you.

| Variable | Default | Meaning |
|---|---|---|
| `HARNESS_SERVER_TOKEN` | required | Every request sends `Authorization: Bearer <token>` |
| `PORT`, `HOST` | `8090`, `127.0.0.1` | `PORT=0` picks a free port |
| `GATEWAY_URL` | `http://localhost:8080` | The HTTP Gateway |
| `WORKSPACE_DIR` | a new temporary directory | pi's working directory. `/workspace` in the sandbox |
| `SESSION_DIR` | a new temporary directory | Where pi writes the session file. It stays outside the workspace, so it never lands in the repo |
| `HARNESS_ALLOW_BASH` | off | `1` lets a profile name the `bash` tool |
| `HEARTBEAT_SECONDS` | `5` | How often a `heartbeat` event goes out while a run is active |

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
  "token": "<the run's gateway token>",
  "engine": "claude",
  "model": "claude-haiku-4-5"
}
```

`harness_session_id` becomes pi's session id and the gateway's prompt cache
key. The token never appears in run state, events or logs.

### Status and end reason

| `status` | `end_reason` | When |
|---|---|---|
| `CREATING` | | The session is being created |
| `RUNNING` | | pi is working |
| `FINISHED` | `completed` | pi ended its turn with no error |
| `ERROR` | `model_error` | A gateway call failed. pi's retry is off, so the run ends and the library's fallback chain is the only retry |
| `ERROR` | `max_turns` | A turn started past the profile's `max_turns` |
| `ERROR` | `harness_error` | pi or the server threw |
| `ERROR` | `server_shutdown` | The server got SIGTERM or SIGINT during the run |
| `CANCELED` | `canceled` | `POST /cancel` |
| `EXPIRED` | `max_minutes` | The run passed the profile's `max_minutes` |

### Events

Each event has `id`, `type`, `run_id`, `time` and `data`.

| `type` | `data` |
|---|---|
| `turn_started` | `turn` |
| `model_call` | The served `engine` and `model`, `served_by_fallback`, `finish_reason`, `tool_calls`, input, output, cached and cache-write tokens, `usd`, `cache_hit`, `latency_ms`. A failed call has `error` and `aborted` |
| `tool_call` | `tool_call_id`, `tool`, `args` |
| `tool_result` | `tool_call_id`, `tool`, `is_error`, `output`, `duration_ms` |
| `file_changed` | `path`, `tool`, after a `write` or `edit` that succeeded |
| `compacted` | `reason` |
| `turn_finished` | `turn`, `stop_reason`, the assistant `text`, the number of `tool_calls` |
| `heartbeat` | `turns`, `elapsed_ms` |
| `run_finished` | `status`, `end_reason`, `error`, `turns`, `elapsed_ms`, `usage` |

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
  `ls`, `find`, `grep`, and `bash` when allowed.
- **The task text as given.** `prompt()` runs with `expandPromptTemplates: false`.

## Tests

```bash
npm test
```

The tests start the server against a fake gateway, so they call no model and
cost nothing. They cover:

- the bearer token
- a full run, with its typed events, the file it wrote and its usage totals
- re-attaching with the same `run_id`, which starts no new model call
- a second session refused with `409`
- a follow-up in the same session, with its own token and the earlier turns
- `Last-Event-ID` resume
- cancel during a model call
- the turn limit
- a failed gateway call
- request checks
- SIGTERM during a run, which exits within seconds

## Not built yet

Each of these is in the design and missing here:

- **Run tokens.** The gateway does not mint them yet, so `token` is a
  gateway API key and the body names `engine` and `model`.
- **Restoring a saved session** into a fresh sandbox with `switchSession()`.
  `/followup` works only on the session this server still holds.
- **The owned tools** `run_tests`, `open_pr` and `ask_for_followup`, the
  `tests_run` event, and the tool-call hook for protected paths, plan-first
  and test-before-PR.
- **The compaction handler** (`session_before_compact`) and the profile's
  compaction settings. pi compacts on its own defaults.
- **`guardrail_blocked`,** which needs the gateway to report blocked tool
  results.
- **Thinking level.** Fixed at `off`.
- **Input during a run** with `steer()` or `followUp()`.
- **`GET /metrics`,** the container image, and the repo clone at `/workspace`.
- **A size limit on the event log,** which lives in memory for the life of
  the server.
