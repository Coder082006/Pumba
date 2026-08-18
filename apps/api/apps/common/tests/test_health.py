"""Health endpoint tests — SRS §37.1.

The endpoint's job is to tell a load balancer whether this instance can serve
traffic. The cases that matter are the failure ones: a probe that reports
healthy while the database is unreachable is worse than no probe at all,
because it keeps a dead instance in rotation.

The dependency checks are patched rather than exercised against real services
so these run without Docker. A genuine end-to-end check against live Postgres
and Redis belongs in the integration suite.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIRequestFactory

from apps.common import views
from apps.common.views import HealthView


def _get():
    request = APIRequestFactory().get("/api/v1/health")
    return HealthView.as_view()(request)


class TestHealthStatus:
    def test_reports_ok_when_every_dependency_is_up(self, monkeypatch) -> None:
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_cache", lambda: (True, None))

        response = _get()
        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert response.data["checks"]["database"]["ok"] is True
        assert response.data["checks"]["cache"]["ok"] is True

    @pytest.mark.parametrize("failing", ["_check_database", "_check_cache"])
    def test_returns_503_when_any_dependency_is_down(self, monkeypatch, failing: str) -> None:
        """503 is what removes the instance from the load balancer's rotation."""
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_cache", lambda: (True, None))
        monkeypatch.setattr(views, failing, lambda: (False, "OperationalError"))

        response = _get()
        assert response.status_code == 503
        assert response.data["status"] == "degraded"

    def test_names_the_failing_dependency(self, monkeypatch) -> None:
        monkeypatch.setattr(views, "_check_database", lambda: (False, "OperationalError"))
        monkeypatch.setattr(views, "_check_cache", lambda: (True, None))

        response = _get()
        assert response.data["checks"]["database"]["error"] == "OperationalError"
        assert response.data["checks"]["cache"]["error"] is None


class TestHealthAccess:
    def test_is_unauthenticated(self, monkeypatch) -> None:
        """A load balancer and an external uptime check both call this."""
        monkeypatch.setattr(views, "_check_database", lambda: (True, None))
        monkeypatch.setattr(views, "_check_cache", lambda: (True, None))
        assert _get().status_code == 200


class TestDependencyProbes:
    def test_database_probe_reports_the_exception_class_not_its_message(self, monkeypatch) -> None:
        """The message could carry a connection string or credentials."""

        class ProbeFailureError(Exception):
            pass

        def explode():
            raise ProbeFailureError("password authentication failed for user 'pumba'")

        monkeypatch.setattr(views, "connection", type("C", (), {"cursor": staticmethod(explode)}))
        ok, error = views._check_database()

        assert ok is False
        assert error == "ProbeFailureError"

    def test_cache_probe_detects_a_silent_readback_failure(self, monkeypatch) -> None:
        """A cache that accepts writes and returns nothing is not usable, even
        though neither call raised."""

        class DeadCache:
            @staticmethod
            def set(*args: object, **kwargs: object) -> None: ...

            @staticmethod
            def get(*args: object, **kwargs: object) -> None:
                return None

        monkeypatch.setattr(views, "cache", DeadCache)
        ok, error = views._check_cache()

        assert ok is False
        assert error == "ReadbackMismatch"

    def test_cache_probe_reports_an_exception_class(self, monkeypatch) -> None:
        class ExplodingCache:
            @staticmethod
            def set(*args: object, **kwargs: object) -> None:
                raise ConnectionError("redis down")

            @staticmethod
            def get(*args: object, **kwargs: object) -> None: ...

        monkeypatch.setattr(views, "cache", ExplodingCache)
        ok, error = views._check_cache()

        assert ok is False
        assert error == "ConnectionError"
