# Runs the sample end to end with one command: `make run` (or just `make`).
#
# 1. Switches to the Node version in .nvmrc through nvm.
# 2. Installs packages when node_modules is missing or older than the lockfile.
# 3. Uses the HTTP Gateway already answering at GATEWAY_URL. If none answers,
#    starts one from the ai-api-unified-http checkout at GATEWAY_DIR, logs it
#    to GATEWAY_LOG, and stops it when the run ends.
# 4. Runs the sample.
#
# Another model: make run SAMPLE_ENGINE=openai SAMPLE_MODEL=gpt-4o-mini
# Variables set on the make command line reach the sample's environment.

SHELL := /bin/bash

GATEWAY_DIR ?= $(abspath $(CURDIR)/../../../aiapi/ai-api-unified-http)
GATEWAY_PORT ?= 8080
GATEWAY_URL ?= http://localhost:$(GATEWAY_PORT)
GATEWAY_API_KEY ?= local-dev-key
GATEWAY_LOG ?= $(CURDIR)/gateway.log
GATEWAY_WAIT_SECONDS ?= 60
NVM_DIR ?= $(HOME)/.nvm

.PHONY: run

run:
	@if [ -s "$(NVM_DIR)/nvm.sh" ]; then \
	  source "$(NVM_DIR)/nvm.sh"; \
	  nvm use >/dev/null || { echo "[make] Node $$(cat .nvmrc) is not installed. Run: nvm install $$(cat .nvmrc)"; exit 1; }; \
	fi; \
	set -eo pipefail; \
	echo "[make] node $$(node --version)"; \
	if [ ! -f node_modules/.package-lock.json ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then \
	  npm ci; \
	fi; \
	health() { curl -fsS "$(GATEWAY_URL)/healthz" 2>/dev/null; }; \
	if health >/dev/null; then \
	  echo "[make] using the gateway already running at $(GATEWAY_URL)"; \
	else \
	  [ -d "$(GATEWAY_DIR)" ] || { echo "[make] no gateway checkout at $(GATEWAY_DIR). Set GATEWAY_DIR=<path to ai-api-unified-http>"; exit 1; }; \
	  echo "[make] starting the gateway from $(GATEWAY_DIR), log: $(GATEWAY_LOG)"; \
	  set -m; \
	  $(MAKE) --no-print-directory -C "$(GATEWAY_DIR)" serve PORT=$(GATEWAY_PORT) DEV_API_KEY=$(GATEWAY_API_KEY) >"$(GATEWAY_LOG)" 2>&1 & \
	  gateway_pid=$$!; \
	  set +m; \
	  trap 'kill -TERM -- -'"$$gateway_pid"' 2>/dev/null || true; echo "[make] gateway stopped"' EXIT; \
	  trap 'exit 130' INT TERM; \
	  for second in $$(seq 1 $(GATEWAY_WAIT_SECONDS)); do \
	    health >/dev/null && break; \
	    kill -0 "$$gateway_pid" 2>/dev/null || { echo "[make] the gateway exited during startup. See $(GATEWAY_LOG)"; exit 1; }; \
	    sleep 1; \
	  done; \
	  health >/dev/null || { echo "[make] the gateway did not answer within $(GATEWAY_WAIT_SECONDS)s. See $(GATEWAY_LOG)"; exit 1; }; \
	fi; \
	echo "[make] gateway: $$(health)"; \
	GATEWAY_URL="$(GATEWAY_URL)" GATEWAY_API_KEY="$(GATEWAY_API_KEY)" npm start
