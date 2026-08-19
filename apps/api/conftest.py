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
def _reset_event_subscribers():
    """The event bus is process-global; a subscriber registered by one test
    must not fire during another."""
    from apps.common.events import clear_subscribers

    clear_subscribers()
    yield
    clear_subscribers()
