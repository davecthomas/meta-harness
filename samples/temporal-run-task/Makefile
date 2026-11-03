# Runs the meta-harness end to end, as far as it is built. stack.sh does the work.
#
#   make run                     start everything, run one task, stop everything
#   make run TASK="..."          the same, with your own task text
#   make cancel-demo             make run, then `temporal workflow cancel` once the worker log shows CANCEL_AT
#   make restart-demo            make run, then SIGKILL the worker once the worker log shows RESTART_AT
#
# Or leave the services running and send them tasks:
#
#   make up                      start Temporal, the gateway and a worker
#   make task TASK="..." REF=x   run one task and print its events; Ctrl-C cancels it
#   make tasks                   list tasks
#   make status REF=x            one task's state, cost and result
#   make watch REF=x             print a running task's events
#   make cancel REF=x            cancel a running task
#   make down                    stop what `make up` started
#
# Each task gets its own harness server. Its log and pi's workspace are in runs/<REF>/.
# Another model: make run ENGINE=openai MODEL=gpt-4o-mini

SHELL := /bin/bash

HARNESS_DIR ?= $(abspath $(CURDIR)/../../images/harness-pi)
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
ENGINE ?= claude
MODEL ?= claude-haiku-4-5
TASK ?=
REF ?=
NVM_DIR ?= $(HOME)/.nvm
UV ?= $(or $(shell command -v uv),$(HOME)/.local/bin/uv)
DEMO ?=
CANCEL_AT ?= turn 1 starts
RESTART_AT ?= turn 2 starts

export HARNESS_DIR HARNESS_SERVER_TOKEN GATEWAY_DIR GATEWAY_PORT GATEWAY_URL GATEWAY_API_KEY \
  TEMPORAL_PORT TEMPORAL_ADDRESS TEMPORAL_UI_URL TEMPORAL_DB WAIT_SECONDS ENGINE MODEL TASK REF \
  NVM_DIR UV DEMO CANCEL_AT RESTART_AT

.PHONY: run cancel-demo restart-demo up down task tasks status watch cancel

run:
	@./stack.sh run

cancel-demo:
	@DEMO=cancel ./stack.sh run

restart-demo:
	@DEMO=restart ./stack.sh run

up:
	@./stack.sh up

down:
	@./stack.sh down

task:
	@./stack.sh task

tasks:
	@.venv/bin/python -m run_task.tasks list

status watch cancel:
	@.venv/bin/python -m run_task.tasks $@ "$$REF"
