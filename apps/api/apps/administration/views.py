"""Interface layer (SRS §8.2 layer 1).

§27.11's tariff console. Every view does three things and no more: validate a
shape, call one service function, render what comes back.

**§27.1's obligations apply to all of them.** The administration console is
"reachable only from allow-listed networks, requiring mandatory TOTP, with
every action written to `audit_log`". The first two are deployment and
authentication concerns and are enforced where they belong; the third is
`services`', inside the transaction that made the change.

**None of these is a `ScopedQuerysetMixin` view, and that is recorded rather
than assumed.** §12.4 makes tariffs administrator-owned — "not provider-quoted
per booking" — and §26.4 states the provider half: "transfer pricing is
platform-managed and is displayed read-only". So every role that can reach
these holds `Scope.GLOBAL` over them, a filter here would match every row while
reporting a control to the §37.2 matrix, and the matrix records them in
`GLOBAL_BY_ROLE` where the claim is re-derived from `OWNERSHIP` on every run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.administration import serializers as ser
from apps.administration import services
from apps.common.authentication import principal_from_request
from apps.common.envelope import success_envelope
from apps.common.permissions import CATALOGUE_ADMIN_PERMISSIONS

__all__ = [
    "AdminCorridorCreateView",
    "AdminCorridorDetailView",
    "AdminTariffCreateView",
    "AdminTariffDetailView",
    "AdminQuotePreviewView",
]

_TAGS = ["Administration"]


class _AdminView(APIView):
    """§5.2: `CATALOGUE_ADMIN` manages "countries, regions, destinations,
    attractions, activities, **tariffs**, policies", globally."""

    permission_classes = CATALOGUE_ADMIN_PERMISSIONS

    def _ip(self, request: Request) -> str | None:
        """The peer address from the socket, never from a header.

        `X-Forwarded-For` is client-controlled, and trusting it would let an
        administrator forge the address in their own §41.13 entry — which is
        the one audit field nobody would think to doubt.
        """
        return request.META.get("REMOTE_ADDR")

    def _validated(self, request: Request, serializer: Any, *, partial: bool) -> dict[str, Any]:
        payload = serializer(data=request.data, partial=partial)
        payload.is_valid(raise_exception=True)
        return dict(payload.validated_data)


class AdminCorridorCreateView(_AdminView):
    @extend_schema(
        request=ser.CorridorWriteSerializer,
        responses={201: ser.CorridorReadSerializer},
        summary="Create a transfer corridor",
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        dto = services.create_corridor(
            fields=self._validated(request, ser.CorridorWriteSerializer, partial=False),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(
            success_envelope(dict(ser.CorridorReadSerializer(dto).data)),
            status=status.HTTP_201_CREATED,
        )


class AdminCorridorDetailView(_AdminView):
    @extend_schema(
        request=ser.CorridorWriteSerializer,
        responses={200: ser.CorridorReadSerializer},
        summary="Amend a transfer corridor",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        """A partial update — including the one that retires a corridor.

        §27.11 has no separate deactivate action and this needs none:
        withdrawing a price is `is_active` moving in this one call, which means
        there is no second path that could record the change differently or not
        at all.
        """
        dto = services.update_corridor(
            public_id,
            fields=self._validated(request, ser.CorridorWriteSerializer, partial=True),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.CorridorReadSerializer(dto).data)))


class AdminTariffCreateView(_AdminView):
    @extend_schema(
        request=ser.TariffWriteSerializer,
        responses={201: ser.TariffReadSerializer},
        summary="Create a metered transfer tariff",
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        dto = services.create_tariff(
            fields=self._validated(request, ser.TariffWriteSerializer, partial=False),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(
            success_envelope(dict(ser.TariffReadSerializer(dto).data)),
            status=status.HTTP_201_CREATED,
        )


class AdminTariffDetailView(_AdminView):
    @extend_schema(
        request=ser.TariffWriteSerializer,
        responses={200: ser.TariffReadSerializer},
        summary="Amend a metered transfer tariff",
        tags=_TAGS,
    )
    def patch(self, request: Request, public_id: UUID) -> Response:
        dto = services.update_tariff(
            public_id,
            fields=self._validated(request, ser.TariffWriteSerializer, partial=True),
            principal=principal_from_request(request),
            ip=self._ip(request),
        )
        return Response(success_envelope(dict(ser.TariffReadSerializer(dto).data)))


class AdminQuotePreviewView(_AdminView):
    """§27.11: "a quote-preview tool for any origin-destination-class
    combination".

    A POST rather than a GET, and it changes nothing. The body carries a
    pickup instant and a party, which are awkward in a query string and would
    end up cached by something; more importantly, a preview whose URL could be
    bookmarked would eventually be pasted somewhere as a price.
    """

    @extend_schema(
        request=ser.QuotePreviewSerializer,
        responses={200: ser.QuotePreviewResultSerializer},
        summary="Preview what a route would be quoted at, and why",
        description=(
            "Runs the tourist's own SRS 12.4 resolution ladder and reports "
            "which rule answered and on which rung. A fare that fell through "
            "to the country default when a corridor was expected is a "
            "misconfiguration indistinguishable from a correct answer without "
            "that information."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request) -> Response:
        result = services.preview_quote(
            self._validated(request, ser.QuotePreviewSerializer, partial=False)
        )
        return Response(success_envelope(dict(ser.QuotePreviewResultSerializer(result).data)))
