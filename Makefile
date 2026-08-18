# One-command developer entrypoints — SRS §37.1: "A new engineer runs the
# stack locally with one command."
#
# On Windows, GNU make is not installed by default. Every target below has an
# equivalent pnpm script; see README.md.

SHELL := /bin/sh
COMPOSE := docker compose
API := cd apps/api &&

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# The one command
# ---------------------------------------------------------------------------
.PHONY: dev
dev: ## Bring up the whole stack (postgres, redis, api, worker, beat, both web apps)
	$(COMPOSE) up --build

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: reset
reset: ## Stop the stack and destroy its volumes (DESTRUCTIVE — drops the local database)
	$(COMPOSE) down --volumes

.PHONY: logs
logs: ## Tail logs from every service
	$(COMPOSE) logs -f

.PHONY: shell
shell: ## Django shell inside the api container
	$(COMPOSE) exec api python manage.py shell

.PHONY: psql
psql: ## psql session against the local database
	$(COMPOSE) exec postgres psql -U pumba -d pumba

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: install
install: ## Install backend and frontend dependencies
	$(API) uv sync
	pnpm install

# ---------------------------------------------------------------------------
# Verification — `make check` is what CI runs
# ---------------------------------------------------------------------------
.PHONY: check
check: lint typecheck boundaries test ## Run every check CI runs

.PHONY: lint
lint: ## ruff + eslint
	$(API) uv run ruff check .
	$(API) uv run ruff format --check .
	pnpm -r lint

.PHONY: format
format: ## Apply ruff and prettier formatting
	$(API) uv run ruff check --fix .
	$(API) uv run ruff format .
	pnpm exec prettier --write "**/*.{ts,tsx,json,md,yaml,yml}"

.PHONY: typecheck
typecheck: ## mypy + tsc
	$(API) uv run mypy apps ports config
	pnpm -r typecheck

.PHONY: boundaries
boundaries: ## Verify the SRS §6.4 module dependency contracts
	$(API) uv run lint-imports

.PHONY: test
test: ## Run the backend and frontend suites
	$(API) uv run pytest
	pnpm -r test

.PHONY: coverage
coverage: ## Backend tests with both SRS §35.3 coverage gates
	$(API) uv run pytest --cov --cov-report=term-missing --cov-fail-under=80
	$(API) uv run coverage report \
		--include="apps/*/domain/*,apps/common/money.py,apps/common/state_machine.py" \
		--fail-under=95

# ---------------------------------------------------------------------------
# Contracts — regenerate whenever the API surface changes
# ---------------------------------------------------------------------------
.PHONY: openapi
openapi: ## Regenerate the committed OpenAPI specification
	$(API) DJANGO_SETTINGS_MODULE=config.settings.ci uv run python manage.py spectacular \
		--file ../../packages/contracts/openapi/openapi.yaml --validate --fail-on-warn

.PHONY: contracts
contracts: openapi ## Regenerate the specification and the TypeScript types
	pnpm --filter @pumba/contracts generate

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply migrations
	$(COMPOSE) exec api python manage.py migrate

.PHONY: makemigrations
makemigrations: ## Generate migrations
	$(COMPOSE) exec api python manage.py makemigrations
