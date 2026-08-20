# One-command developer entrypoints — SRS §37.1: "A new engineer runs the
# stack locally with one command."
#
# On Windows, GNU make is not installed by default. Every target below has an
# equivalent pnpm script; see README.md.

SHELL := /bin/sh
COMPOSE := docker compose
API := cd apps/api &&

# Anything that *imports* Django settings runs in the api container. GeoDjango
# binds GDAL, GEOS and PROJ through ctypes at import time (ADR 0009), and this
# host has none of them — so mypy, the backend suite and `spectacular` all fail
# here and pass in CI, which is the wrong way round. `ruff` and `lint-imports`
# read source without executing it, so they stay on the host and stay fast.
API_IN := $(COMPOSE) run --rm api

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
# Verification
#
#   make check    the edit loop: lint, types, boundaries, both suites, build
#   make verify   what CI runs, command for command. Run this before pushing.
#
# The two exist because they failed differently once. `next build` type-checks
# route modules against Next's Page contract, and `tsc --noEmit` does not: a
# page exporting anything beyond its default component and Next's own fields is
# valid TypeScript and an invalid route. That error was invisible to every
# local gate and arrived as a GitHub email, having been red for a phase. So the
# build is now part of `check`, and everything else CI does — the coverage
# thresholds, the two staleness diffs, the SAST pass — is part of `verify`.
#
# The rule of thumb: `check` while working, `verify` before pushing.
# ---------------------------------------------------------------------------
.PHONY: check
check: lint typecheck boundaries test ## Lint, types, boundaries, both suites and the web builds

.PHONY: verify
verify: ## Everything CI runs, in CI's order. Run before pushing.
	@echo "==> ruff (lint)"
	$(API) uv run ruff check .
	@echo "==> ruff (format)"
	$(API) uv run ruff format --check .
	@echo "==> mypy"
	$(API_IN) uv run mypy apps ports config
	@echo "==> import-linter"
	$(API) uv run lint-imports
	@echo "==> pytest with both SRS 35.3 coverage gates"
	$(MAKE) coverage
	@echo "==> OpenAPI is in sync"
	$(MAKE) openapi
	@git diff --exit-code -- packages/contracts/openapi/openapi.yaml \
		|| { echo "openapi.yaml is stale; commit the regenerated file"; exit 1; }
	@echo "==> generated contract types are in sync"
	pnpm --filter @pumba/contracts generate
	@git diff --exit-code -- packages/contracts \
		|| { echo "contract types are stale; commit the regenerated files"; exit 1; }
	@echo "==> frontend"
	pnpm -r typecheck
	pnpm -r lint
	pnpm -r test
	pnpm -r build
	@echo "==> bandit (SAST)"
	$(API_IN) uv run --with bandit bandit -r apps ports config -ll
	@echo "OK — every CI gate passed locally."

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
typecheck: ## mypy (in the api container) + tsc
	$(API_IN) uv run mypy apps ports config
	pnpm -r typecheck

.PHONY: boundaries
boundaries: ## Verify the SRS §6.4 module dependency contracts
	$(API) uv run lint-imports

# The backend suite runs inside the api container: GeoDjango binds GDAL, GEOS
# and PROJ natively and the image already carries them (ADR 0009). It also
# gives the suite parity with the runtime, which is worth keeping even once a
# host GDAL install is routine.
#
# `pnpm -r build` is here rather than in a target of its own because it is the
# only local gate that checks a Next route module's *shape*. Leaving it to CI
# is how a broken page survived a phase.
.PHONY: test
test: ## Backend suite (in the api container), frontend suites, and both web builds
	$(COMPOSE) run --rm api pytest
	pnpm -r test
	pnpm -r build

.PHONY: test-host
test-host: ## Run the backend suite on the host (needs GDAL, GEOS and PROJ locally)
	$(API) uv run pytest

.PHONY: build
build: ## Build both web apps (the only local check of Next route module shape)
	pnpm -r build

# The `--include` list mirrors the CI job exactly. It drifted once, and a
# coverage gate that measures a different set locally is not a gate.
.PHONY: coverage
coverage: ## Backend tests with both SRS §35.3 coverage gates
	$(COMPOSE) run --rm api pytest --cov --cov-report=term-missing --cov-fail-under=80
	$(COMPOSE) run --rm api coverage report \
		--include="apps/*/domain/*,apps/common/authz/*,apps/common/money.py,apps/common/state_machine.py" \
		--fail-under=95

# ---------------------------------------------------------------------------
# Contracts — regenerate whenever the API surface changes
# ---------------------------------------------------------------------------
.PHONY: openapi
openapi: ## Regenerate the committed OpenAPI specification
	$(COMPOSE) run --rm -e DJANGO_SETTINGS_MODULE=config.settings.ci api python manage.py spectacular \
		--file /contracts/openapi/openapi.yaml --validate --fail-on-warn

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
