"""Health and public configuration — the two routes that need no principal.

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

from apps.common.envelope import success_envelope
from apps.common.public_config import public_config
from apps.common.throttling import CatalogueReadThrottle

__all__ = ["HealthView", "ConfigView"]


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


class ConfigView(APIView):
    """GET /api/v1/config — SRS §23.13, §24.1, §35.

    §24.1 makes this the first call every client makes: the splash screen
    resolves configuration *before showing anything else*, and blocks on a
    forced upgrade when the client is below `min_supported_version`. The driver
    app's splash (§26.1) calls it too.

    Unauthenticated by necessity rather than by choice — a client that has not
    signed in still needs to know whether it is too old to run, and a version
    floor that only reachable after login cannot retire a broken client
    generation.

    **What it may say is decided in `public_config.py`, not here.** This view
    calls one function and serialises what it returns. It never reaches for
    `get_setting` itself, because a view that could would make the allow-list
    advisory — and the table behind it holds every dispatch weight, fraud
    threshold and commission rate on the platform.
    """

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    # §24.1 has every client calling this on launch, so it is the most-hit
    # unauthenticated route on the platform. Shares the catalogue's per-IP
    # bucket: it is the same population of unauthenticated callers.
    throttle_classes = [CatalogueReadThrottle]

    @extend_schema(
        operation_id="config",
        summary="Client configuration and feature flags",
        description=(
            "Resolves the values a client needs before it can show anything: "
            "the minimum supported client version (SRS §23.13), the currencies "
            "a tourist may choose, the base-map tile URL and its required "
            "attribution, the BR-101 bound on a stay's length, and the "
            "feature-flag set (§35). "
            "The response carries an explicit allow-list of settings and never "
            "the wider `system_setting` register."
        ),
        auth=[],
        responses={
            200: inline_serializer(
                name="ConfigResponse",
                fields={
                    "min_supported_version": serializers.CharField(),
                    "enabled_currencies": serializers.ListField(child=serializers.CharField()),
                    "map_tile_url": serializers.CharField(),
                    "map_tile_attribution": serializers.CharField(),
                    "stay_max_nights": serializers.IntegerField(),
                    "features": serializers.DictField(child=serializers.BooleanField()),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        return Response(success_envelope(public_config()))
