# The meta-harness control plane, run locally.
#
#   make up        start Postgres (Docker Compose) and the Temporal dev server
#   make migrate   apply every component's schema migrations
#   make serve     start the control-plane services (event service, task service, planner) in the background
#   make stop      stop them
#   make test      unit tests, plus the database tests when Postgres is up
#   make lint      ruff
#   make down      stop what `make up` started; Postgres keeps its data
#   make clean-db  stop Postgres and delete its data
#
# The zero-cost end-to-end check still lives in samples/temporal-run-task (make e2e there).

SHELL := /bin/bash
# Local settings and secrets' locations (see .env.example). Git ignores .env.
-include .env
export MH_GITHUB_APPS
STACK_DIR := $(CURDIR)/stack
MH_POSTGRES_PORT ?= 5444
MH_TEMPORAL_PORT ?= 7233
export MH_DATABASE_URL ?= postgresql://mh:mh@127.0.0.1:$(MH_POSTGRES_PORT)/mh
export MH_POSTGRES_PORT
# Local development keys only. In the cloud these come from Secret Manager.
export MH_TASK_API_KEYS ?= local:local-task-key
export MH_SERVICE_KEYS ?= local-service:local-service-key
export MH_TASK_API_KEY ?= local-task-key
export MH_SERVICE_KEY ?= local-service-key
export MH_OPERATOR_KEYS ?= local-operator:local-operator-key
export MH_OPERATOR_KEY ?= local-operator-key

.PHONY: up down clean-db migrate serve stop test lint

up:
	@mkdir -p $(STACK_DIR)
	docker compose up -d --wait postgres
	@if [ -f $(STACK_DIR)/temporal.pid ] && kill -0 $$(cat $(STACK_DIR)/temporal.pid) 2>/dev/null; then \
		echo "up: the Temporal dev server is already running"; \
	else \
		temporal server start-dev --port $(MH_TEMPORAL_PORT) --headless --log-level warn \
			>$(STACK_DIR)/temporal.log 2>&1 & echo $$! >$(STACK_DIR)/temporal.pid; \
		echo "up: Temporal dev server on localhost:$(MH_TEMPORAL_PORT), log in stack/temporal.log"; \
	fi
	@echo "up: Postgres at $(MH_DATABASE_URL)"

down:
	@if [ -f $(STACK_DIR)/temporal.pid ]; then kill $$(cat $(STACK_DIR)/temporal.pid) 2>/dev/null || true; rm -f $(STACK_DIR)/temporal.pid; fi
	docker compose stop postgres

clean-db:
	docker compose down --volumes

migrate:
	uv run mh migrate

serve: migrate
	scripts/services.sh start

stop:
	scripts/services.sh stop

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
