"""Pytest configuration.

Integration tests that need real PostgreSQL and Redis are marked
`@pytest.mark.integration` and provision them with testcontainers, which
requires Docker. They are skipped automatically when Docker is unavailable so
the pure-domain suite — the 95%-coverage layer — still runs everywhere.
"""

from __future__ import annotations

import shutil

import pytest


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if _docker_available():
        return
    skip = pytest.mark.skip(reason="Docker unavailable; integration tests need testcontainers")
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
