"""booking module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.4.5's one endpoint.

**The path says `trips` and the code lives here** — ADR 0022. §6.4 forbids
`trip -> inventory`, and quoting locks inventory counters, so the use case
belongs to the module that may see both. Routing is an interface concern and
`config/urls.py` already composes the API from four modules; moving the *path*
to match the *package* would make §9.4.5, §42's FR-030 and every sequence
diagram wrong in order to protect an implementation detail.

The view is thin like every other: parse, call one service function,
serialise. `InventoryUnavailableError` and `ConflictError` are deliberately not
caught — §8.7's hierarchy carries the status and §9.2's handler builds the
envelope, `details` array and all, so a `try/except` here would be a second
place deciding what a sold-out departure's HTTP status is.
"""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.booking import serializers as ser
from apps.booking import services
from apps.common.envelope import success_envelope
from apps.common.permissions import IsTourist, tourist_id_of
from apps.common.throttling import TripQuoteThrottle

__all__ = ["TripQuoteView"]


class TripQuoteView(APIView):
    """`POST /trips/{id}/quote` — §9.4.5.

    Authenticated, a tourist, and throttled at §9.6's *20 / hour / trip*: this
    is the most expensive endpoint in the system and the only one that takes
    row locks on shared counters, so a client retrying in a loop would contend
    with every other tourist quoting the same departure.

    `IsTourist` answers only "is the caller a tourist at all", which discloses
    nothing about any particular trip. Whether it is *their* trip is the
    service's `tourist_id` filter, and a stranger gets 404 rather than 403
    (§30.3).
    """

    permission_classes = [IsTourist]
    throttle_classes = [TripQuoteThrottle]

    @extend_schema(
        request=None,
        responses={200: ser.QuoteSerializer},
        summary="Price a trip and hold its capacity",
        description=(
            "Converts a plan into a priced, inventory-backed, time-boxed "
            "offer — SRS §9.4.5.\n\n"
            "Every activity on the itinerary is resolved to the departure its "
            "start instant names, and capacity for the whole party is held "
            "against each **under a row lock** (§17.3). The figures returned "
            "here are authoritative in a way the ones from "
            "`GET /activities/{id}/departures` are not: those may be stale, "
            "these were true at the moment the lock was held.\n\n"
            "The hold lasts `quote.ttl_minutes` — twenty by default — and is "
            "released automatically when it expires, at which point the trip "
            "returns to `DRAFT` and can be edited again.\n\n"
            "A re-quote **releases this trip's own prior holds first**, so "
            "asking twice does not make a trip compete with itself for the "
            "last seats.\n\n"
            "**409 `INVENTORY_UNAVAILABLE`** carries a `details` array naming "
            "every departure that could not be held, why, and any alternative "
            "departures of the same activity. **409 `TRIP_NOT_QUOTABLE`** "
            "means the itinerary has not been planned, has blocking "
            "validation errors, or is in a state past pricing."
        ),
        tags=["trip"],
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        result = services.quote_trip(public_id, tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.QuoteSerializer(result).data))
