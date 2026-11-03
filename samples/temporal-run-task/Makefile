# Runs the sample end to end with one command: `make run` (or just `make`).
#
# 1. Installs the Python packages with uv, and the harness server's Node
#    packages with npm under the Node version in its .nvmrc.
# 2. Uses the Temporal server already answering at TEMPORAL_ADDRESS. If none
#    answers, starts `temporal server start-dev` and stops it at the end.
# 3. Uses the HTTP Gateway already answering at GATEWAY_URL. If none answers,
#    starts one from the ai-api-unified-http checkout at GATEWAY_DIR and stops
#    it at the end.
# 4. Starts a new harness server from images/harness-pi. A harness server holds
#    one session, so every run gets its own.
# 5. Starts the worker, shows its log, and runs the starter.
# 6. On exit or Ctrl-C, stops everything it started.
#
# Logs: temporal.log, gateway.log, harness-server.log and worker.log in this directory.
#
# Demos of the same run:
#   make cancel-demo    cancels the workflow with `temporal workflow cancel`
#                       when the worker log shows CANCEL_AT
#   make restart-demo   kills the worker with SIGKILL when the worker log shows
#                       RESTART_AT, then starts a new worker
#
# Another model: make run SAMPLE_ENGINE=openai SAMPLE_MODEL=gpt-4o-mini

SHELL := /bin/bash

HARNESS_DIR ?= $(abspath $(CURDIR)/../../images/harness-pi)
HARNESS_PORT ?= 8090
HARNESS_URL ?= http://127.0.0.1:$(HARNESS_PORT)
HARNESS_SERVER_TOKEN ?= local-harness-token
GATEWAY_DIR ?= $(abspath $(CURDIR)/../../../aiapi/ai-api-unified-http)
GATEWAY_PORT ?= 8080
GATEWAY_URL ?= http://localhost:$(GATEWAY_PORT)
GATEWAY_API_KEY ?= local-dev-key
TEMPORAL_PORT ?= 7233
TEMPORAL_ADDRESS ?= localhost:$(TEMPORAL_PORT)
TEMPORAL_UI_URL ?= http://localhost:$(shell echo $$(($(TEMPORAL_PORT) + 1000)))
TEMPORAL_DB ?=
WAIT_SECONDS ?= 60
SAMPLE_ENGINE ?= claude
SAMPLE_MODEL ?= claude-haiku-4-5
NVM_DIR ?= $(HOME)/.nvm
UV ?= $(or $(shell command -v uv),$(HOME)/.local/bin/uv)
DEMO ?=
CANCEL_AT ?= turn 1 starts
RESTART_AT ?= turn 2 starts

export HARNESS_URL HARNESS_SERVER_TOKEN GATEWAY_URL GATEWAY_API_KEY TEMPORAL_ADDRESS TEMPORAL_UI_URL SAMPLE_ENGINE SAMPLE_MODEL

.PHONY: run cancel-demo restart-demo

run:
	@if [ -s "$(NVM_DIR)/nvm.sh" ]; then \
	  source "$(NVM_DIR)/nvm.sh"; \
	  (cd "$(HARNESS_DIR)" && nvm use >/dev/null) || { echo "[make] Node $$(cat "$(HARNESS_DIR)/.nvmrc") is not installed. Run: nvm install $$(cat "$(HARNESS_DIR)/.nvmrc")"; exit 1; }; \
	else \
	  echo "[make] nvm is not installed at $(NVM_DIR). The harness server runs under the Node version in its .nvmrc."; exit 1; \
	fi; \
	set -eo pipefail; \
	temporal_pid=; gateway_pid=; harness_pid=; worker_pid=; tail_pid=; \
	stop_group() { kill -TERM -- "-$$1" 2>/dev/null || true; wait "$$1" 2>/dev/null || true; echo "[make] $$2 stopped"; }; \
	stop_all() { \
	  [ -z "$$worker_pid" ] || stop_group "$$worker_pid" worker; \
	  [ -z "$$tail_pid" ] || { sleep 1; kill "$$tail_pid" 2>/dev/null || true; wait "$$tail_pid" 2>/dev/null || true; }; \
	  [ -z "$$harness_pid" ] || stop_group "$$harness_pid" "harness server"; \
	  [ -z "$$gateway_pid" ] || stop_group "$$gateway_pid" gateway; \
	  [ -z "$$temporal_pid" ] || stop_group "$$temporal_pid" "Temporal dev server"; \
	}; \
	trap stop_all EXIT; \
	trap 'exit 130' INT TERM; \
	wait_for() { \
	  for second in $$(seq 1 $(WAIT_SECONDS)); do \
	    "$$2" && return 0; \
	    kill -0 "$$3" 2>/dev/null || { echo "[make] $$1 exited during startup. See $$4"; return 1; }; \
	    sleep 1; \
	  done; \
	  echo "[make] $$1 did not answer within $(WAIT_SECONDS)s. See $$4"; return 1; \
	}; \
	start_worker() { \
	  set -m; .venv/bin/python -m run_task.worker >>worker.log 2>&1 & worker_pid=$$!; set +m; \
	  worker_ready() { [ "$$(grep -c '^\[worker\] polling' worker.log)" -gt "$$1" ]; }; \
	  ready_count=$$(grep -c '^\[worker\] polling' worker.log || true); \
	  for second in $$(seq 1 $(WAIT_SECONDS)); do worker_ready "$$ready_count" && return 0; kill -0 "$$worker_pid" 2>/dev/null || break; sleep 1; done; \
	  echo "[make] the worker did not start. See worker.log"; return 1; \
	}; \
	"$(UV)" sync --quiet; \
	if [ ! -f "$(HARNESS_DIR)/node_modules/.package-lock.json" ] || [ "$(HARNESS_DIR)/package-lock.json" -nt "$(HARNESS_DIR)/node_modules/.package-lock.json" ]; then \
	  (cd "$(HARNESS_DIR)" && nvm use >/dev/null && npm ci); \
	fi; \
	temporal_up() { temporal operator cluster health --address "$(TEMPORAL_ADDRESS)" >/dev/null 2>&1; }; \
	if temporal_up; then \
	  echo "[make] using the Temporal server already running at $(TEMPORAL_ADDRESS)"; \
	else \
	  command -v temporal >/dev/null || { echo "[make] the Temporal CLI is not installed. Run: brew install temporal"; exit 1; }; \
	  echo "[make] starting the Temporal dev server on port $(TEMPORAL_PORT), log: $(CURDIR)/temporal.log"; \
	  set -m; temporal server start-dev --port $(TEMPORAL_PORT) $(if $(TEMPORAL_DB),--db-filename "$(TEMPORAL_DB)") >temporal.log 2>&1 & temporal_pid=$$!; set +m; \
	  wait_for "the Temporal dev server" temporal_up "$$temporal_pid" temporal.log; \
	fi; \
	gateway_up() { curl -fsS "$(GATEWAY_URL)/healthz" >/dev/null 2>&1; }; \
	if gateway_up; then \
	  echo "[make] using the gateway already running at $(GATEWAY_URL)"; \
	else \
	  [ -d "$(GATEWAY_DIR)" ] || { echo "[make] no gateway checkout at $(GATEWAY_DIR). Set GATEWAY_DIR=<path to ai-api-unified-http>"; exit 1; }; \
	  echo "[make] starting the gateway from $(GATEWAY_DIR), log: $(CURDIR)/gateway.log"; \
	  set -m; $(MAKE) --no-print-directory -C "$(GATEWAY_DIR)" serve PORT=$(GATEWAY_PORT) DEV_API_KEY=$(GATEWAY_API_KEY) >gateway.log 2>&1 & gateway_pid=$$!; set +m; \
	  wait_for "the gateway" gateway_up "$$gateway_pid" gateway.log; \
	fi; \
	echo "[make] gateway: $$(curl -fsS "$(GATEWAY_URL)/healthz")"; \
	harness_up() { curl -fsS -H "Authorization: Bearer $(HARNESS_SERVER_TOKEN)" "$(HARNESS_URL)/health" >/dev/null 2>&1; }; \
	if curl -s -o /dev/null "$(HARNESS_URL)/health" 2>/dev/null; then \
	  echo "[make] something already answers at $(HARNESS_URL). Stop it, or set HARNESS_PORT to a free port."; exit 1; \
	fi; \
	echo "[make] starting the harness server from $(HARNESS_DIR), log: $(CURDIR)/harness-server.log"; \
	set -m; (cd "$(HARNESS_DIR)" && nvm use >/dev/null && PORT=$(HARNESS_PORT) HEARTBEAT_SECONDS=2 exec node server/main.ts) >harness-server.log 2>&1 & harness_pid=$$!; set +m; \
	wait_for "the harness server" harness_up "$$harness_pid" harness-server.log; \
	echo "[make] harness server: $$(curl -fsS -H "Authorization: Bearer $(HARNESS_SERVER_TOKEN)" "$(HARNESS_URL)/health")"; \
	: >worker.log; \
	tail -n +1 -F worker.log 2>/dev/null & tail_pid=$$!; \
	start_worker; \
	wait_for_log() { \
	  for tick in $$(seq 1 $$(( $(WAIT_SECONDS) * 5 ))); do grep -qF -- "$$1" worker.log && return 0; sleep 0.2; done; \
	  echo "[make] worker.log never showed: $$1"; return 1; \
	}; \
	workflow_id="run-task-$$(uuidgen | cut -c1-8 | tr 'A-Z' 'a-z')"; \
	case "$(DEMO)" in \
	  cancel) \
	    .venv/bin/python -m run_task.starter --workflow-id "$$workflow_id" & starter_pid=$$!; \
	    wait_for_log '$(CANCEL_AT)'; \
	    echo "[make] temporal workflow cancel --workflow-id $$workflow_id"; \
	    temporal workflow cancel --address "$(TEMPORAL_ADDRESS)" --workflow-id "$$workflow_id" >/dev/null; \
	    status=0; wait "$$starter_pid" || status=$$?; \
	    [ "$$status" -eq 3 ] || { echo "[make] expected the workflow to end canceled, starter exit $$status"; exit 1; } ;; \
	  restart) \
	    .venv/bin/python -m run_task.starter --workflow-id "$$workflow_id" & starter_pid=$$!; \
	    wait_for_log '$(RESTART_AT)'; \
	    echo "[make] killing the worker with SIGKILL (process group $$worker_pid). The harness server keeps running pi."; \
	    kill -KILL -- "-$$worker_pid"; wait "$$worker_pid" 2>/dev/null || true; \
	    echo "[make] Temporal retries run_harness once 10s pass with no heartbeat, plus the 2s retry interval"; \
	    echo "[make] starting a new worker"; \
	    start_worker; \
	    wait "$$starter_pid" ;; \
	  *) .venv/bin/python -m run_task.starter --workflow-id "$$workflow_id" ;; \
	esac

cancel-demo:
	@$(MAKE) --no-print-directory run DEMO=cancel

restart-demo:
	@$(MAKE) --no-print-directory run DEMO=restart
