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
#   make platform       apply the platform Terraform stack by hand: a new project, or a plan the deploy's guard refuses
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

.PHONY: up down clean-db migrate serve stop test lint console console-dev platform deploy deploy-status need-settings \
	gcp-status gcp-backup gcp-pause gcp-resume gcp-disable gcp-teardown

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

# The deployment's settings: one file names the project, region, zone, state bucket and console site.
# The file is local: the repo is public, so it is git-ignored (deploy/environment.example.json shows its shape).
SETTINGS := deploy/environment.json
setting = $(shell [ -f $(SETTINGS) ] && jq -r '.$(1) // empty' $(SETTINGS))
# The targets that act on the deployment stop when the file is missing, saying how to make it.
need-settings:
	@[ -f $(SETTINGS) ] || { echo "error: $(SETTINGS) is missing. Copy deploy/environment.example.json to it and fill in the deployment's project, or ask whoever runs the deployment for theirs." >&2; exit 1; }
PROJECT := $(call setting,project)
REGION := $(call setting,region)
ZONE := $(call setting,zone)
STATE_BUCKET := $(call setting,state_bucket)
CONSOLE_SITE := $(call setting,console_site)
# deploy/.env holds what stays out of the public repo, such as who may sign in (deploy/.env.example).
-include deploy/.env
export TF_VAR_operators TF_VAR_admins

# Terraform acts as the account gcloud is signed in as, rather than whatever application default
# credentials happen to hold (often another account). Before anything is created, that account must be able
# to read the settings file's project; Terraform shows the plan and asks before applying it.
platform: export GOOGLE_OAUTH_ACCESS_TOKEN = $(shell gcloud auth print-access-token)
platform: need-settings
	@[ -n "$(PROJECT)" ] && [ -n "$(REGION)" ] && [ -n "$(STATE_BUCKET)" ] || { echo "error: $(SETTINGS) must name project, region and state_bucket." >&2; exit 1; }
	@[ -n "$$TF_VAR_operators" ] || { echo "error: who may sign in is not set. Copy deploy/.env.example to deploy/.env and fill in TF_VAR_operators." >&2; exit 1; }
	@gcloud projects describe $(PROJECT) --format="value(projectId)" >/dev/null 2>&1 || { echo "error: $$(gcloud config get-value account 2>/dev/null) cannot read project $(PROJECT). Sign in to gcloud as an owner of it." >&2; exit 1; }
	@echo "platform: project $(PROJECT), state gs://$(STATE_BUCKET), as $$(gcloud config get-value account 2>/dev/null)"
	# The stack enables the APIs it uses, but reading the project to plan needs these two first: in a new
	# project they are off.
	gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project $(PROJECT)
	gcloud storage buckets describe gs://$(STATE_BUCKET) --project $(PROJECT) >/dev/null 2>&1 || \
	  gcloud storage buckets create gs://$(STATE_BUCKET) --project $(PROJECT) --location $(REGION) \
	    --uniform-bucket-level-access --public-access-prevention
	terraform -chdir=deploy/terraform/platform init -input=false -reconfigure -backend-config=bucket=$(STATE_BUCKET)
	# The APIs first, on their own: the stack's resources do not wait for them, so in a new project they would
	# be created before their API is on and fail. In a project that has them, this changes nothing.
	terraform -chdir=deploy/terraform/platform apply -input=false -var-file=$(CURDIR)/$(SETTINGS) -target=google_project_service.this
	terraform -chdir=deploy/terraform/platform apply -input=false -var-file=$(CURDIR)/$(SETTINGS)

deploy:
	gh workflow run deploy --ref main
	sleep 5
	gh run watch $$(gh run list --workflow deploy --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status

deploy-status: need-settings
	@echo "VM booted: $$(gcloud compute instances get-guest-attributes mh-vm --zone $(ZONE) --project $(PROJECT) --query-path mh/boot --format='value(value)' 2>/dev/null || echo nothing yet)"
	@echo "console:   https://$(CONSOLE_SITE).web.app"

# Leaving the Google Cloud project (deploy/shutdown.sh says what each does). gcp-disable and gcp-teardown each do
# the whole job in one command: back up the database, stop the meta-harness, then turn billing off or delete
# the project. Both ask once for the project id before changing anything.
gcp-status gcp-backup gcp-pause gcp-resume: need-settings
	deploy/shutdown.sh $(@:gcp-%=%)
gcp-disable: need-settings
	deploy/shutdown.sh disable
gcp-teardown: need-settings
	deploy/shutdown.sh delete
