"""Health endpoint — the only route Phase 1 exposes.

SRS §37.1 acceptance: "a single health endpoint passing the full pipeline".

Reports liveness of the process and readiness of the two backing services the
API cannot serve without. Deliberately unauthenticated and uncached: a load
balancer and an uptime check must both be able to call it.
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

__all__ = ["HealthView"]


def _check_database() -> tuple[bool, str | None]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        return False, type(exc).__name__
    return True, None


def _check_cache() -> tuple[bool, str | None]:
    try:
        cache.set("health:probe", "1", timeout=5)
        if cache.get("health:probe") != "1":
            return False, "ReadbackMismatch"
    except Exception as exc:
        return False, type(exc).__name__
    return True, None


class HealthView(APIView):
    """GET /api/v1/health"""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []

    @extend_schema(
        operation_id="health",
        summary="Liveness and readiness probe",
        description=(
            "Reports process liveness and the reachability of PostgreSQL and "
            "Redis. Returns 503 when any dependency is unreachable so that a "
            "load balancer removes the instance from rotation."
        ),
        auth=[],
        responses={
            200: inline_serializer(
                name="HealthResponse",
                fields={
                    "status": serializers.CharField(),
                    "version": serializers.CharField(),
                    "checks": serializers.DictField(),
                },
            ),
            503: inline_serializer(
                name="HealthUnavailableResponse",
                fields={
                    "status": serializers.CharField(),
                    "version": serializers.CharField(),
                    "checks": serializers.DictField(),
                },
            ),
        },
        tags=["system"],
    )
    def get(self, request: Request) -> Response:
        db_ok, db_error = _check_database()
        cache_ok, cache_error = _check_cache()

        checks: dict[str, dict[str, Any]] = {
            "database": {"ok": db_ok, "error": db_error},
            "cache": {"ok": cache_ok, "error": cache_error},
        }
        healthy = db_ok and cache_ok

        return Response(
            {
                "status": "ok" if healthy else "degraded",
                "version": "1.0.0",
                "checks": checks,
            },
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
