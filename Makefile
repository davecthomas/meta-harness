# The meta-harness control plane, run locally.
#
#   make up        start Postgres (Docker Compose) and the Temporal dev server
#   make migrate   apply every component's schema migrations
#   make serve     build the console, then start the control-plane services in the background: event
#                  service, task service, planner, scheduler, and the local edge, which serves the
#                  console at http://127.0.0.1:8300 and routes /v1 to the services as you
#   make stop      stop them
#   make console   build the console web app (React and TypeScript) into console/dist
#   make console-dev  the console with hot reload at http://127.0.0.1:5173, over the edge
#   make test      unit tests, plus the database tests when Postgres is up
#   make lint      ruff
#   make down      stop what `make up` started; Postgres keeps its data
#   make clean-db  stop Postgres and delete its data
#
# The zero-cost end-to-end check still lives in samples/temporal-run-task (make e2e there).
#
# Google Cloud (deploy/README.md). Every merge to main deploys itself (.github/workflows/deploy.yml).
#   make platform       apply the platform Terraform stack: network, identity, secrets' containers, disk
#   make deploy         deploy main by hand: runs the deploy workflow and follows it
#   make deploy-status  the commit the VM last booted, and the console's URL

SHELL := /bin/bash
# Local settings and secrets' locations (see .env.example). Git ignores .env.
-include .env
export MH_GITHUB_APPS MH_CONSOLE_OPERATORS MH_CONSOLE_DEV_IDENTITY ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_GEMINI_API_KEY GOOGLE_APPLICATION_CREDENTIALS
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

.PHONY: up down clean-db migrate serve stop test lint console console-dev platform deploy deploy-status

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

serve: migrate console
	scripts/services.sh start

console:
	cd console && npm ci --no-audit --no-fund --silent && npm run build --silent

console-dev:
	cd console && npm run dev

stop:
	scripts/services.sh stop

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

STATE_BUCKET := meta-harness-prod-terraform
# Terraform acts as the account gcloud is signed in as, rather than whatever application default
# credentials happen to hold (often another account).
platform: export GOOGLE_OAUTH_ACCESS_TOKEN = $(shell gcloud auth print-access-token)
platform:
	gcloud storage buckets describe gs://$(STATE_BUCKET) --project meta-harness-prod >/dev/null 2>&1 || \
	  gcloud storage buckets create gs://$(STATE_BUCKET) --project meta-harness-prod --location us-central1 \
	    --uniform-bucket-level-access --public-access-prevention
	terraform -chdir=deploy/terraform/platform init -input=false -reconfigure
	terraform -chdir=deploy/terraform/platform apply -input=false

deploy:
	gh workflow run deploy --ref main
	sleep 5
	gh run watch $$(gh run list --workflow deploy --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status

deploy-status:
	@echo "VM booted: $$(gcloud compute instances get-guest-attributes mh-vm --zone us-central1-a --project meta-harness-prod --query-path mh/boot --format='value(value)' 2>/dev/null || echo nothing yet)"
	@echo "console:   $$(gcloud run services describe mh-edge --region us-central1 --project meta-harness-prod --format='value(status.url)' 2>/dev/null || echo not deployed)"
