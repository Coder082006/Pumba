"""Pytest configuration.

Integration tests needing real PostgreSQL are marked `@pytest.mark.integration`
and are skipped when no database is reachable, so the pure-domain suite — the
95%-coverage layer — still runs on a machine with nothing installed.

The gate is **reachability, not the presence of the `docker` CLI**. Those two
stopped being the same thing when the suite moved into the api container
(ADR 0009): the container has Postgres on the compose network and no `docker`
binary, so a CLI check skipped four real transaction tests while the database
they needed was one hostname away. A skip that depends on how the suite was
launched rather than on what it can reach is a skip that hides tests.
"""

from __future__ import annotations

import os
import socket

import pytest

_PROBE_TIMEOUT_SECONDS = 3.0


def _database_reachable() -> bool:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _database_reachable():
        return
    skip = pytest.mark.skip(reason="no PostgreSQL reachable; integration tests need one")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_request_context():
    """Keep contextvars from leaking between tests."""
    from apps.common.context import reset_context

    reset_context()
    yield
    reset_context()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Rate-limit buckets live in the cache, and the cache is process-wide.

    Without this, the sixty-first request any test makes to a §9.6-throttled
    endpoint gets a 429 — and which test that is depends on collection order,
    so the failure moves every time somebody adds a test. A throttle test that
    wants a full bucket fills it itself, within one test.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _reset_event_subscribers():
    """The event bus is process-global; a subscriber registered by one test
    must not fire during another."""
    from apps.common.events import clear_subscribers

    clear_subscribers()
    yield
    clear_subscribers()


@pytest.fixture(autouse=True)
def _ports_are_always_fakes(settings) -> None:  # type: ignore[no-untyped-def]
    """No test may reach a real provider — rule 13, SRS §34.8.

    Rule 13 requires every vendor adapter to have "a fake for tests", and this
    is what makes that true rather than merely available. `PORT_ADAPTERS` is
    read from the environment so a deployment can select an adapter without a
    code change (`config/settings/base.py`), and `docker-compose.yml` passes
    the variables straight through — so the moment a developer put real SMTP
    credentials in `.env` to try the verification email, the suite started
    resolving `SmtpEmailAdapter` and attempting to send. It was caught by
    fakes-only assertions failing on `.sent`; the next such port might simply
    have worked, quietly, against somebody's live account.

    Emptying the map sends every port back to its fake in
    `apps.common.ports_registry`. Cleared on the way in and out because the
    resolution is `@cache`d per process.
    """
    from apps.common.ports_registry import reset_ports

    settings.PORT_ADAPTERS = {}
    reset_ports()
    yield
    reset_ports()
