# Runs the meta-harness end to end, as far as it is built. stack.sh does the work.
#
#   make run                     start everything, run one task, stop everything
#   make run TASK="..."          the same, with your own task text
#   make cancel-demo             make run, then `temporal workflow cancel` once the worker log shows CANCEL_AT
#   make restart-demo            make run, then SIGKILL the worker once the worker log shows RESTART_AT
#   make spend-limit-demo        make run with a $0.001 spend limit, which the run passes on turn 1
#   make fallback-demo           make run on tiers whose first candidates cannot start, so the run
#                                loop tries the next candidate, then escalates to the next tier
#   make egress-check            check the per-run sandbox network rules, with no model call
#   make e2e                     the zero-cost end-to-end check: make run, the demos and egress-check
#                                against a fake HTTP Gateway that makes no model calls (CI runs it)
#   make run GATEWAY=fake        any command against the fake gateway, at no cost
#
# Or leave the services running and send them tasks:
#
#   make up                      start Temporal, Redis, the gateway and a worker
#   make task TASK="..." REF=x   run one task and print its events; Ctrl-C cancels it
#   make tasks                   list tasks
#   make status REF=x            one task's state, cost and result
#   make watch REF=x             print a running task's events
#   make cancel REF=x            cancel a running task
#   make down                    stop what `make up` started
#
# Each run gets its own per-run sandbox: a Docker container of the harness-pi image on its
# own internal network, started and removed by the run activity. Its only way out is the
# egress proxy, which allows EGRESS_ALLOW. Docker must be running.
# The harness server's log and pi's workspace are in runs/<REF>/.
# The ai-api-unified router picks each task's tier from config/models.json, and the run loop
# tries the tier's candidates (harness profile and model) in order. A classifier needs
# OPENAI_API_KEY in this shell; without it every task takes the default tier.
# Bound the tier: make run FLOOR=complex, or CEILING=standard
# One pair, no routing: make run PIN=harness:pi-lean/openai:gpt-4o-mini
# Another spend limit for the task's runs together, in USD: make run SPEND_LIMIT=0.05
# A repo task: pi works in a fresh clone, and a finished run becomes a pull request from mh/<REF>:
#   make run REPO=owner/name TASK="..." CHECKS="make test"
#   make run TICKET=owner/name#12           the issue's text and checks; the pull request closes it
#   make repo-demo                          a repo task against a local bare repo: no code host, no PR opened

SHELL := /bin/bash

HARNESS_DIR ?= $(abspath $(CURDIR)/../../images/harness-pi)
HARNESS_IMAGE ?= harness-pi:dev
EGRESS_IMAGE ?= mh-egress-proxy:dev
RUNS_DIR ?= $(CURDIR)/runs
GATEWAY_DIR ?= $(abspath $(CURDIR)/../../../aiapi/ai-api-unified-http)
# real: ai-api-unified-http from GATEWAY_DIR. fake: run_task/fake_gateway.py, which makes no model calls.
GATEWAY ?= real
# The fake binds here. CI on Linux sets 0.0.0.0, so the egress proxy container can reach it.
FAKE_GATEWAY_HOST ?= 127.0.0.1
FAKE_TURN_SECONDS ?= 2
# make e2e runs the fake on its own port, so it never meets a real gateway on 8080.
E2E_GATEWAY_PORT ?= 8099
GATEWAY_PORT ?= 8080
GATEWAY_URL ?= http://localhost:$(GATEWAY_PORT)
GATEWAY_API_KEY ?= local-dev-key
# The gateway as the egress proxy reaches it: host.docker.internal is this machine.
SANDBOX_GATEWAY_URL ?= http://host.docker.internal:$(GATEWAY_PORT)
# host:port patterns a per-run sandbox may reach through the egress proxy: the HTTP Gateway,
# the code host and package registries. `*` is a wildcard.
EGRESS_ALLOW ?= host.docker.internal:$(GATEWAY_PORT),github.com:443,*.github.com:443,*.githubusercontent.com:443,registry.npmjs.org:443,pypi.org:443,files.pythonhosted.org:443
# The gateway keeps run grants here. Not 6379, so a Redis you run yourself is left alone.
REDIS_PORT ?= 6390
REDIS_URL ?= redis://127.0.0.1:$(REDIS_PORT)/0
TEMPORAL_PORT ?= 7233
TEMPORAL_ADDRESS ?= localhost:$(TEMPORAL_PORT)
TEMPORAL_UI_URL ?= http://localhost:$(shell echo $$(($(TEMPORAL_PORT) + 1000)))
TEMPORAL_DB ?=
WAIT_SECONDS ?= 60
ROUTER_CONFIG ?= $(CURDIR)/config/models.json
# An entry under `environments` in ROUTER_CONFIG. Empty uses the file as it is, or zero-cost with GATEWAY=fake.
MODELS_ENV ?=
PIN ?=
FLOOR ?=
CEILING ?=
REPO ?=
TICKET ?=
CHECKS ?=
SPEND_LIMIT ?= 0.50
TASK ?=
REF ?=
UV ?= $(or $(shell command -v uv),$(HOME)/.local/bin/uv)
DEMO ?=
CANCEL_AT ?= turn 1 starts
RESTART_AT ?= turn 2 starts

export GATEWAY FAKE_GATEWAY_HOST FAKE_TURN_SECONDS HARNESS_DIR HARNESS_IMAGE EGRESS_IMAGE EGRESS_ALLOW RUNS_DIR GATEWAY_DIR GATEWAY_PORT GATEWAY_URL GATEWAY_API_KEY \
  SANDBOX_GATEWAY_URL REDIS_PORT REDIS_URL TEMPORAL_PORT TEMPORAL_ADDRESS TEMPORAL_UI_URL TEMPORAL_DB \
  WAIT_SECONDS ROUTER_CONFIG MODELS_ENV PIN FLOOR CEILING REPO TICKET CHECKS SPEND_LIMIT TASK REF UV DEMO CANCEL_AT RESTART_AT

.PHONY: run cancel-demo restart-demo spend-limit-demo fallback-demo repo-demo egress-check e2e up down task tasks status watch cancel

run:
	@./stack.sh run

cancel-demo:
	@DEMO=cancel ./stack.sh run

restart-demo:
	@DEMO=restart ./stack.sh run

# Turn 1 starts below the limit and passes it, so the gateway refuses turn 2.
spend-limit-demo:
	@DEMO=spend-limit SPEND_LIMIT=0.001 ./stack.sh run

# The fallback-demo entry in config/models.json: standard's candidates name a harness profile
# whose image does not exist, and complex has one that works.
fallback-demo:
	@DEMO=fallback MODELS_ENV=fallback-demo ./stack.sh run

# A local bare repo with one commit stands in for the code host.
repo-demo:
	@DEMO=repo ./stack.sh run

egress-check:
	@./stack.sh egress-check

# Target-specific values, so GATEWAY_URL, SANDBOX_GATEWAY_URL and EGRESS_ALLOW follow the fake's port.
e2e: GATEWAY = fake
e2e: GATEWAY_PORT = $(E2E_GATEWAY_PORT)
e2e:
	@./e2e.sh

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
