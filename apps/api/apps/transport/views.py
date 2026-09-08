"""Interface layer (SRS §8.2 layer 1).

`GET /transport/vehicle-classes` — §9.3.4, auth column `—`, so public.

**One route, and the other two are elsewhere on purpose.** §9.3.4 lists three
API-04 endpoints. This is the only one that names no `catalogue` row: a corridor
names two destinations and a quote resolves four kinds of binding, and §6.4
gives `transport -> location, provider`, so neither can be served from here.
`trip` serves both, because it is the one module that may see both sides
(ADR 0023). The URLs give no sign of the split, which is the point — a client
reads §9.3.4 and finds three endpoints under `transport/`.

Public and unauthenticated because §24.16 shows vehicle-class cards to a
tourist who has not signed in, and because the payload is a closed set of four
seeded rows describing capacity. There is nothing here to scope against and
nothing to disclose.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.envelope import success_envelope
from apps.common.throttling import CatalogueReadThrottle
from apps.transport import services
from apps.transport.serializers import VehicleClassSerializer

__all__ = ["VehicleClassListView"]


@extend_schema(
    responses={200: VehicleClassSerializer(many=True)},
    summary="List vehicle classes",
    description=(
        "The vehicle classes a transfer may be quoted for, with seat and "
        "luggage capacity. Capacity only: a class has no price of its own — a "
        "route priced for a class does — so fares come from "
        "POST /transport/quotes, which knows the leg (SRS 12.4, 24.16)."
    ),
    tags=["Transport"],
)
class VehicleClassListView(APIView):
    """Unauthenticated, throttled, and unpaginated.

    Unpaginated is a decision rather than an omission, and the same one
    `TagListView` made: this is a closed vocabulary an administrator seeds —
    Appendix C says four rows — and §24.16 renders it as a row of cards. A
    cursor here would make a front end loop to draw four cards and would make
    the response uncacheable as a whole for no benefit.
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [CatalogueReadThrottle]

    def get(self, request: Request) -> Response:
        return Response(
            success_envelope(
                [dict(VehicleClassSerializer(row).data) for row in services.vehicle_classes()]
            )
        )
