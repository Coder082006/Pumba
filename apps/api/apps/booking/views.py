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

from typing import Any
from uuid import UUID

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.booking import selectors, services
from apps.booking import serializers as ser
from apps.booking.domain.lifecycle import Actor
from apps.common.authentication import principal_from_request
from apps.common.authz import Permission, Role
from apps.common.envelope import success_envelope
from apps.common.errors import NotFoundError
from apps.common.idempotency import IDEMPOTENCY_HEADER, idempotent
from apps.common.permissions import (
    HasPermission,
    IsAuthenticatedPrincipal,
    IsTourist,
    tourist_id_of,
)
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

    **`Idempotency-Key` is required** — §9.4.5 says so in as many words, and
    this is the endpoint where it earns the requirement. The quote takes row
    locks, holds seats and starts a twenty-minute clock; a client whose request
    timed out and retried would otherwise hold a second set of seats against
    the same trip and be told the first offer had gone. The throttle above
    limits how often that can happen and does not stop it happening once.
    """

    permission_classes = [IsTourist]
    throttle_classes = [TripQuoteThrottle]

    @extend_schema(
        request=None,
        parameters=[
            OpenApiParameter(
                name=IDEMPOTENCY_HEADER,
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "A client-generated key, at most 64 characters, unique to this "
                    "attempt. Repeating a request with the same key returns the "
                    "first response and takes no further capacity; reusing one for "
                    "a different trip is `409 IDEMPOTENCY_KEY_REUSED`. Kept for "
                    "`idempotency.retention_hours`. A failed attempt releases its "
                    "key, so a retry after `409 INVENTORY_UNAVAILABLE` may reuse it."
                ),
            )
        ],
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
            "validation errors, or is in a state past pricing.\n\n"
            "**`Idempotency-Key` is required.** Retrying with the same key "
            "replays the first answer rather than holding a second set of "
            "seats; a second request arriving while the first is still running "
            "is `409 IDEMPOTENT_REQUEST_IN_PROGRESS` and is safe to retry."
        ),
        tags=["trip"],
    )
    @idempotent
    def post(self, request: Request, public_id: UUID) -> Response:
        result = services.quote_trip(public_id, tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.QuoteSerializer(result).data))


class TripConfirmView(APIView):
    """§9.4.6, `POST /trips/{id}/confirm`: turn an accepted quote into a basket."""

    permission_classes = [IsTourist]
    throttle_classes = [TripQuoteThrottle]

    @extend_schema(
        request=ser.ConfirmSerializer,
        parameters=[
            OpenApiParameter(
                name=IDEMPOTENCY_HEADER,
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
                description=(
                    "A client-generated key unique to this attempt. Repeating the "
                    "request with the same key returns the first basket and creates "
                    "no second one."
                ),
            )
        ],
        responses={201: ser.BasketSerializer},
        summary="Create the booking basket from an accepted quote",
        description=(
            "SRS §9.4.6. Creates one booking per component in `PENDING`, "
            "snapshots each component's cancellation policy and commission rate, "
            "moves the trip to `PENDING_PAYMENT` and extends the held capacity to "
            "the payment window. **Nothing is committed and no provider is "
            "notified** — that happens on payment capture.\n\n"
            "**409 `QUOTE_EXPIRED`**: the quote lapsed or was superseded; no "
            "booking is created. **409 `TRIP_NOT_PAYABLE`**: the trip is not "
            "priced, or has nothing bookable. **409 `NOT_BOOKABLE`**: a "
            "component has no verified seller or starts in the past; `details` "
            "names each. **409 `PRICE_CHANGED`**: a transfer fare moved since the "
            "quote. **409 `HOLD_EXPIRED`**: held capacity lapsed."
        ),
        tags=["trip"],
    )
    @idempotent
    def post(self, request: Request, public_id: UUID) -> Response:
        body = ser.ConfirmSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        principal = principal_from_request(request)
        basket = services.create_basket(
            public_id,
            tourist_id=tourist_id_of(request),
            quote_token=body.validated_data["quote_token"],
            actor_user_id=None if principal is None else principal.user_id,
        )
        return Response(
            success_envelope(ser.BasketSerializer(basket).data), status=status.HTTP_201_CREATED
        )


class TripCancelView(APIView):
    """§9.3 `POST /trips/{id}/cancel` — "Cancel entire trip subject to policy".

    Moved here from `trip` in Phase 7. A trip with bookings cannot be cancelled
    by the module that may not see them: flipping the trip's status alone left
    its bookings CONFIRMED and their seats sold. The path is unchanged; the
    route name is now `v1:booking:trip-cancel` (ADR 0022's pattern).
    """

    permission_classes = [IsTourist]

    @extend_schema(
        request=None,
        responses={200: ser.TripCancellationSerializer},
        summary="Cancel a whole trip, each component under its own policy",
        description=(
            "BR-046: every live component is evaluated against its own "
            "snapshotted policy and the refund is itemised. If any component can "
            "no longer be cancelled (it has started), nothing is cancelled and "
            "the response is 409 CANCELLATION_NOT_PERMITTED naming each one."
        ),
        tags=["trip"],
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        principal = principal_from_request(request)
        result = services.cancel_trip(
            public_id,
            tourist_id=tourist_id_of(request),
            actor_user_id=None if principal is None else principal.user_id,
        )
        return Response(success_envelope(ser.TripCancellationSerializer(result).data))


class TripCancellationPreviewView(APIView):
    """What cancelling the whole trip would refund, before anybody does it.

    §20.9 promises "the tourist always sees the financial consequence before
    acting" and itemises trip-level cancellation, but §9.3 lists a preview only
    per booking. A trip-level preview is the same promise at the level the
    cancel button acts on; it writes nothing.
    """

    permission_classes = [IsTourist]

    @extend_schema(
        responses={200: ser.TripCancellationSerializer},
        summary="Preview cancelling a whole trip",
        tags=["trip"],
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        result = services.preview_trip_cancellation(public_id, tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.TripCancellationSerializer(result).data))


# -- API-05 (§9.3.5) ------------------------------------------------------------------


def _visible(request: Request, public_id: UUID, *, write: bool = False) -> Any:
    """The booking, through the principal-scoped selector, or 404 (§30.3)."""
    row = selectors.one_visible_to(principal_from_request(request), public_id, write=write)
    if row is None:
        raise NotFoundError()
    return row


class BookingListView(APIView):
    """`GET /bookings` — T/P/D, "List, scoped by role"."""

    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, required=False),
            OpenApiParameter(
                "trip",
                UUID,
                required=False,
                description="Only one of your trips. §24.23 lists a trip's own bookings.",
            ),
        ],
        responses={200: ser.BookingSerializer(many=True)},
        summary="List the bookings you may see",
        tags=["booking"],
    )
    def get(self, request: Request) -> Response:
        principal = principal_from_request(request)
        rows = selectors.visible_to(principal).order_by("starts_at", "id")
        wanted = request.query_params.get("status")
        if wanted:
            rows = rows.filter(status=wanted)
        trip = request.query_params.get("trip")
        if trip:
            # Resolved through the tourist's own trips, so naming somebody
            # else's trip is the same empty answer as naming none (§30.3).
            trip_id = services.owned_trip_id(
                trip, tourist_id=None if principal is None else principal.tourist_id
            )
            rows = rows.filter(trip_id=trip_id) if trip_id is not None else rows.none()
        dtos = services.list_bookings(list(rows[:200]))
        return Response(success_envelope(ser.BookingSerializer(dtos, many=True).data))


class BookingDetailView(APIView):
    """`GET /bookings/{id}` — "Detail incl. status history and provider contact"."""

    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(
        responses={200: ser.BookingDetailSerializer}, summary="Read a booking", tags=["booking"]
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        detail = services.booking_detail(_visible(request, public_id))
        return Response(success_envelope(ser.BookingDetailSerializer(detail).data))


class BookingCancellationPreviewView(APIView):
    """`GET /bookings/{id}/cancellation-preview` — T, §20.9, BR-043."""

    permission_classes = [IsTourist]

    @extend_schema(
        responses={200: ser.CancellationSerializer},
        summary="What cancelling this booking would refund",
        tags=["booking"],
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        preview = services.preview_cancellation(_visible(request, public_id))
        return Response(success_envelope(ser.CancellationSerializer(preview).data))


class BookingCancelView(APIView):
    """`POST /bookings/{id}/cancel` — T/P, "Cancel with reason; returns computed refund"."""

    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(
        request=ser.CancelRequestSerializer,
        responses={200: ser.CancellationSerializer},
        summary="Cancel a booking",
        description=(
            "A tourist is refunded under the booking's own snapshotted policy "
            "(BR-040); a provider cancelling refunds the tourist in full, fee "
            "included (BR-045). 409 CANCELLATION_NOT_PERMITTED once the booking "
            "has started (BR-042)."
        ),
        tags=["booking"],
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = ser.CancelRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        principal = principal_from_request(request)
        row = _visible(request, public_id, write=True)
        if principal is not None and principal.tourist_id == row.tourist_id:
            actor = Actor.TOURIST
        elif principal is not None and (
            principal.has_role(Role.PROVIDER_OWNER) or principal.has_role(Role.PROVIDER_STAFF)
        ):
            actor = Actor.PROVIDER
        else:
            raise NotFoundError()
        result = services.cancel_booking(
            row.public_id,
            actor=actor,
            actor_user_id=None if principal is None else principal.user_id,
            reason=body.validated_data.get("reason", ""),
        )
        return Response(success_envelope(ser.CancellationSerializer(result).data))


class BookingVoucherView(APIView):
    """`POST /bookings/{id}/voucher` — T, "Generate/download PDF voucher" (ADR 0026)."""

    permission_classes = [IsTourist]

    @extend_schema(
        request=None,
        responses={(200, "application/pdf"): bytes},
        summary="Download this booking's voucher",
        tags=["booking"],
    )
    def post(self, request: Request, public_id: UUID) -> HttpResponse:
        filename, data = services.voucher_document(_visible(request, public_id))
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class _ProviderResponseView(APIView):
    """§26.6: a provider accepts or declines within the response window.

    PROVIDER_BOOKING_MANAGE and the booking's own `provider_id`. There is no
    provider login until Phase 11, so today only SUPER_ADMIN — who holds that
    permission globally — can reach these; the scoping is already the portal's.
    """

    permission_classes = [
        IsAuthenticatedPrincipal,
        HasPermission.for_(Permission.PROVIDER_BOOKING_MANAGE),
    ]


class BookingAcceptView(_ProviderResponseView):
    @extend_schema(
        request=None,
        responses={200: ser.BookingSerializer},
        summary="Accept an on-request booking",
        tags=["booking"],
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        row = _visible(request, public_id, write=True)
        principal = principal_from_request(request)
        result = services.accept_request(
            row.public_id, actor_user_id=None if principal is None else principal.user_id
        )
        return Response(success_envelope(ser.BookingSerializer(result).data))


class BookingDeclineView(_ProviderResponseView):
    @extend_schema(
        request=ser.DeclineRequestSerializer,
        responses={200: ser.BookingSerializer},
        summary="Decline an on-request booking",
        tags=["booking"],
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        body = ser.DeclineRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        row = _visible(request, public_id, write=True)
        principal = principal_from_request(request)
        result = services.decline_request(
            row.public_id,
            actor_user_id=None if principal is None else principal.user_id,
            reason=body.validated_data["reason"],
        )
        return Response(success_envelope(ser.BookingSerializer(result).data))
