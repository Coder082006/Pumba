"""inventory module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). No business logic, no ORM queries.

One endpoint: `GET /activities/{reference}/departures`. ADR 0011 put it here
rather than in `catalogue`, and the reason is the whole shape of this file:

    The availability-composing endpoints move up a layer … served from
    `apps/inventory/views.py`, reading catalogue through its service interface,
    because `catalogue` may not compose inventory data.

**The activity is resolved through `catalogue.services`, with visibility.**
`resolve_listing_ref` walks the country → region → market → destination chain
and returns nothing for a listing that is withdrawn or whose market has not
launched. So a hidden activity's calendar answers 404, identically to an
activity that never existed — §30.3's rule, arrived at from the public side.

**Unauthenticated, and cached briefly.** §9.3.2 makes the catalogue public and
§8.10 allows a 60-second cache here while forbidding one anywhere near a
booking: *"a cached availability figure may never confirm a booking."* The
payload says `INDICATIVE` for exactly that reason, and the cache is what makes
the label load-bearing rather than decorative.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogue import services as catalogue
from apps.common.envelope import success_envelope
from apps.common.errors import NotFoundError
from apps.common.throttling import CatalogueReadThrottle
from apps.inventory import serializers as ser
from apps.inventory import services

__all__ = ["ActivityDeparturesView"]

#: §24.10 shows the next thirty days when the caller names no window.
DEFAULT_WINDOW_DAYS = 30

#: §8.10's "Availability search result … 60 s. Deliberately short; never used
#: for the authoritative check."
CACHE_SECONDS = 60


@extend_schema(
    parameters=[ser.DepartureQuerySerializer],
    responses={200: ser.DepartureSerializer(many=True)},
    summary="List an activity's departures",
    description=(
        "Sellable departures for one activity, with the seats remaining on "
        "each.\n\n"
        "**These figures are indicative and every row says so.** SRS §17.1 "
        "principle I3: *search may read cached or stale capacity; committing "
        "a booking may not.* The authoritative check happens under a row lock "
        "inside `POST /trips/{id}/quote`, and a `basis` of `INDICATIVE` here "
        "means precisely that this number must not be used to promise "
        "anybody a seat.\n\n"
        "Supplying `pax` turns the list into advice: each row then carries "
        "`unbookable` — `SOLD_OUT`, `PAST_CUTOFF`, `PARTY_TOO_LARGE` and the "
        "rest — so a client can say why a date it is showing cannot be "
        "taken. Cancelled departures are listed rather than hidden: a date "
        "that silently vanishes reads as a bug to somebody who was looking "
        "at it a minute ago."
    ),
    tags=["inventory"],
    auth=[],
)
class ActivityDeparturesView(APIView):
    """SD-06's `GET /activities/{id}/departures?from&to&pax`."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [CatalogueReadThrottle]

    def get(self, request: Request, reference: str) -> Response:
        query = ser.DepartureQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        window = dict(query.validated_data)

        today = timezone.localdate()
        listing = catalogue.resolve_listing_ref("activity", reference, today=today)
        if listing is None:
            # Withdrawn, or in a market that has not launched. §30.3: the same
            # answer as an activity that never existed.
            raise NotFoundError()

        # The window is dates; departures are instants. Resolving one against
        # the other needs the destination's zone, and `place_facts` is the
        # accessor that already answers that without `inventory` reaching into
        # the geography tables.
        place = catalogue.place_facts("activity", [listing.storage_id])[listing.storage_id]

        since = window.get("date_from") or today
        until = window.get("to") or since + timedelta(days=DEFAULT_WINDOW_DAYS)
        pax = window.get("pax")

        key = f"departures:{listing.storage_id}:{since}:{until}:{pax or 0}"
        payload = cache.get(key)
        if payload is None:
            departures = services.list_departures(
                listing.storage_id,
                since=_start_of(since, place.timezone),
                until=_end_of(until, place.timezone),
                now=timezone.now(),
                pax=pax,
            )
            payload = [dict(ser.DepartureSerializer(row).data) for row in departures]
            cache.set(key, payload, CACHE_SECONDS)

        return Response(success_envelope(payload, {"basis": "INDICATIVE"}))


def _start_of(day: date, zone: str) -> datetime:
    """Midnight on `day`, in the destination's zone.

    A window given as dates has to become instants somewhere, and doing it in
    the server's zone would put a Zanzibar morning departure on the previous
    day for anybody asking from west of Greenwich — the calendar would be
    correct and off by one.
    """
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(zone))


def _end_of(day: date, zone: str) -> datetime:
    """The last instant of `day`, so `to=` includes the day it names."""
    return datetime.combine(day, time.max, tzinfo=ZoneInfo(zone))
