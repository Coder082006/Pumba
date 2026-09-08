"""trip module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.4.2's endpoints.

Every view here does three things and no more: parse the request, call one
function in `services`, serialise what comes back. No ORM, no business rule,
no ownership comparison — `services` takes `tourist_id` and filters by it, and
§30.3's "404, never 403" depends on that staying true in exactly one place.

`NotFoundError`, `ConflictError` and `ValidationError` are not caught. §8.7's
hierarchy carries the status code and §9.2's handler turns each into the one
error envelope; a `try/except` here would be a second place deciding what a
locked item's HTTP status is.

**Every response is the whole trip.** A mutation returns the same shape a read
would, rebuilt through the read path, because two shapes for one resource is
how a client starts to disagree with itself about what it has — and §24.14
re-renders the timeline after every edit anyway.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.envelope import success_envelope
from apps.common.errors import NotFoundError
from apps.common.geo import Coordinates
from apps.common.throttling import CatalogueReadThrottle
from apps.trip import serializers as ser
from apps.trip import services, transfers
from apps.trip.permissions import IsTourist, tourist_id_of

__all__ = [
    "TripListCreateView",
    "TripDetailView",
    "TripItemsView",
    "TripItemDetailView",
    "TripFlightsView",
    "TripGenerateView",
    "TripCancelView",
    "TransportCorridorListView",
    "TransportQuoteView",
]


class _TouristView(APIView):
    """Authenticated, and a tourist. Ownership is the service's, not ours."""

    permission_classes = [IsTourist]


def _trip_response(dto: object, code: int = status.HTTP_200_OK) -> Response:
    return Response(success_envelope(ser.TripSerializer(dto).data), status=code)


class TripListCreateView(_TouristView):
    @extend_schema(responses={200: ser.TripSummarySerializer(many=True)})
    def get(self, request: Request) -> Response:
        """§24.20's My Trips — summaries, not full trips."""
        trips = services.list_trips(tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.TripSummarySerializer(trips, many=True).data))

    @extend_schema(request=ser.CreateTripSerializer, responses={201: ser.TripSerializer})
    def post(self, request: Request) -> Response:
        payload = ser.CreateTripSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = services.create_trip(tourist_id=tourist_id_of(request), **payload.validated_data)
        return _trip_response(trip, status.HTTP_201_CREATED)


class TripDetailView(_TouristView):
    @extend_schema(responses={200: ser.TripSerializer})
    def get(self, request: Request, public_id: UUID) -> Response:
        """A trip that is not this tourist's is absent, not forbidden (§30.3).

        `services.get_trip` returns `None` for both a foreign trip and one that
        never existed, and this raises the same error for both — so the two are
        indistinguishable from outside, which is the point.
        """
        trip = services.get_trip(public_id, tourist_id=tourist_id_of(request))
        if trip is None:
            raise NotFoundError(f"no trip {public_id}")
        return _trip_response(trip)

    @extend_schema(request=ser.UpdateTripSerializer, responses={200: ser.TripSerializer})
    def patch(self, request: Request, public_id: UUID) -> Response:
        payload = ser.UpdateTripSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = services.update_trip(
            public_id, tourist_id=tourist_id_of(request), **payload.validated_data
        )
        return _trip_response(trip)


class TripItemsView(_TouristView):
    @extend_schema(request=ser.AddItemSerializer, responses={201: ser.TripSerializer})
    def post(self, request: Request, public_id: UUID) -> Response:
        payload = ser.AddItemSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = services.add_item(
            public_id, tourist_id=tourist_id_of(request), **payload.validated_data
        )
        return _trip_response(trip, status.HTTP_201_CREATED)


class TripItemDetailView(_TouristView):
    @extend_schema(request=ser.UpdateItemSerializer, responses={200: ser.TripSerializer})
    def patch(self, request: Request, public_id: UUID, item_id: UUID) -> Response:
        payload = ser.UpdateItemSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = services.update_item(
            public_id, item_id, tourist_id=tourist_id_of(request), **payload.validated_data
        )
        return _trip_response(trip)

    @extend_schema(request=None, responses={200: ser.TripSerializer})
    def delete(self, request: Request, public_id: UUID, item_id: UUID) -> Response:
        """Returns the trip rather than 204.

        §24.14 re-renders the timeline after a removal — sequence numbers and
        the running total both change — so a 204 would be immediately followed
        by a GET. A locked item is a 409 (§10.8), raised by the service.
        """
        trip = services.remove_item(public_id, item_id, tourist_id=tourist_id_of(request))
        return _trip_response(trip)


class TripFlightsView(_TouristView):
    @extend_schema(request=ser.SetFlightsSerializer, responses={200: ser.TripSerializer})
    def put(self, request: Request, public_id: UUID) -> Response:
        """§9.4.2 is a PUT: the whole set replaces what is there."""
        payload = ser.SetFlightsSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        trip = services.set_flights(
            public_id,
            tourist_id=tourist_id_of(request),
            flights=payload.validated_data["flights"],
        )
        return _trip_response(trip)


class TripGenerateView(_TouristView):
    @extend_schema(request=None, responses={200: ser.TripSerializer})
    def post(self, request: Request, public_id: UUID) -> Response:
        """§10.2's generate. The findings come back on the itinerary, not as
        an error: §10.6 returns them from a run that worked."""
        trip = services.generate_itinerary(public_id, tourist_id=tourist_id_of(request))
        return _trip_response(trip)


class TripCancelView(_TouristView):
    @extend_schema(request=None, responses={200: ser.TripSerializer})
    def post(self, request: Request, public_id: UUID) -> Response:
        """§20.5. A completed trip is a 409, raised by the state machine —
        a journey that has happened cannot be made not to have happened."""
        trip = services.cancel_trip(public_id, tourist_id=tourist_id_of(request))
        return _trip_response(trip)


class TransportCorridorListView(APIView):
    """`GET /transport/corridors` — §9.3.4, auth column `—`, so public.

    Served by `trip` rather than by `transport` because a corridor names two
    destinations and §7.2 forbids their sequential ids reaching a client, so
    rendering one means a `catalogue` read that §6.4 denies `transport`
    (ADR 0023). The path is exactly as §9.3.4 writes it; only the code lives
    elsewhere.

    Unpaginated. Appendix C seeds about thirty of these and they are a route
    map rather than a feed; a cursor would make a client loop to draw a list
    that fits on one screen.
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [CatalogueReadThrottle]

    @extend_schema(
        responses={200: ser.CorridorSerializer(many=True)},
        summary="List transfer corridors",
        description=(
            "The origin-destination pairs the platform runs transfers between, "
            "by vehicle class. No fare: a corridor's price depends on the class "
            "and the date, so fares come from POST /transport/quotes "
            "(SRS 9.3.4, 12.4)."
        ),
        tags=["Transport"],
    )
    def get(self, request: Request) -> Response:
        rows = transfers.corridors()
        return Response(success_envelope([dict(ser.CorridorSerializer(row).data) for row in rows]))


class TransportQuoteView(_TouristView):
    """`POST /transport/quotes` — §9.4.4.

    §24.16's airport-pickup cards and §24.17's per-leg class selection are both
    this endpoint. It prices; it reserves nothing. §9.4.5 is explicit that
    "transfers hold no inventory but reserve a vehicle class, not a specific
    driver", so there is no hold, no token and no clock here — which is also
    why it carries no `Idempotency-Key`: a repeated quote costs nothing and
    changes nothing, and requiring a key would imply otherwise.

    **The trip is named and checked.** §9.4.4's request carries `trip_id`, and
    a quote for somebody else's trip is §30.3's 404 rather than a 403. The
    check is `services.get_trip`, which filters by `tourist_id` — the same one
    place every other trip route depends on.
    """

    @extend_schema(
        request=ser.TransferQuoteRequestSerializer,
        responses={200: ser.LegQuoteSerializer(many=True)},
        summary="Quote one or more transfer legs",
        description=(
            "Prices each leg for every vehicle class the party fits, using the "
            "SRS 12.4 resolution ladder. Returns 422 NO_TARIFF_CONFIGURED when "
            "no corridor or fallback tariff matches, and 502 ROUTING_UNAVAILABLE "
            "when a metered leg cannot be measured — never a guessed price "
            "(SRS 12.6)."
        ),
        tags=["Transport"],
    )
    def post(self, request: Request) -> Response:
        payload = ser.TransferQuoteRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        body = payload.validated_data

        # Ownership, before anything is resolved or priced. A stranger must not
        # be able to use this endpoint to discover that a trip exists.
        if services.get_trip(body["trip_id"], tourist_id=tourist_id_of(request)) is None:
            raise NotFoundError()

        legs = [_leg_spec(leg) for leg in body["legs"]]
        priced = transfers.quote_legs(legs)
        return Response(
            success_envelope([dict(ser.LegQuoteSerializer(leg).data) for leg in priced])
        )


def _endpoint_ref(payload: dict[str, Any]) -> transfers.EndpointRef:
    """§12.2's binding, from the four mutually exclusive fields.

    The serializer has already established that exactly one is present, so the
    first match is the only match — but the iteration order is `BINDINGS` and
    not the payload's, because a dict ordered by whatever the client sent would
    make the answer depend on JSON key order.
    """
    kind = next(key for key in transfers.BINDINGS if payload.get(key))
    latitude, longitude = payload.get("latitude"), payload.get("longitude")
    return transfers.EndpointRef(
        kind=kind,
        reference=payload[kind],
        point=(
            Coordinates(lat=latitude, lon=longitude)
            if latitude is not None and longitude is not None
            else None
        ),
    )


def _leg_spec(payload: dict[str, Any]) -> transfers.LegSpec:
    return transfers.LegSpec(
        reference=payload["reference"],
        origin=_endpoint_ref(payload["origin"]),
        target=_endpoint_ref(payload["target"]),
        depart_at=payload["depart_at"],
        pax=payload["pax"],
        luggage=payload["luggage"],
    )
