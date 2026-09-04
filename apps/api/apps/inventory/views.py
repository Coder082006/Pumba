"""inventory module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). No business logic, no ORM queries.

Two surfaces. `GET /activities/{reference}/departures` is §9.3.2's public
calendar; `/admin/activities/{public_id}/departures` is §26.5's operator one,
and they answer about the same rows with deliberately different payloads.
ADR 0011 put both here rather than in `catalogue`, and the reason is the whole
shape of this file:

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
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogue import services as catalogue
from apps.common.envelope import success_envelope
from apps.common.errors import NotFoundError
from apps.common.permissions import CATALOGUE_ADMIN_PERMISSIONS
from apps.common.throttling import CatalogueReadThrottle
from apps.inventory import serializers as ser
from apps.inventory import services
from apps.inventory.dto import DepartureEdit

__all__ = ["ActivityDeparturesView", "AdminActivityDeparturesView"]

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


# ---------------------------------------------------------------------------
# §26.5 — the availability calendar an operator edits
# ---------------------------------------------------------------------------
#
# **Administrator-owned in v1, provider-owned in Phase 11.** §26.4 puts an
# activity's price, capacity and cut-off with its operator, because they are
# claims only the operator can make. There is no principal that can own one
# yet: `apps/provider/` has no table, `activity.provider_id` is a column
# nothing writes, and a `/provider/...` route could not answer *whose activity
# is this?* — so its authorisation test could not be written, there being no
# second provider to be foreign. ADR 0022 records that.
#
# What it costs is not the capability but the self-service: an operator still
# cannot close Tuesday's boat for weather without asking somebody. Putting the
# calendar on the console that exists now buys that back today, and costs
# Phase 11 nothing, because `Resource.ACTIVITY_DEPARTURE`'s ownership rule is
# already written: `_provider_listed(..., "activity__provider_id")`. When the
# portal arrives the route gains a provider-scoped sibling and the rule is
# already true — the BR-023 logic below, and `services.edit_departures` under
# it, do not move.


@extend_schema_view(
    get=extend_schema(
        parameters=[ser.DepartureQuerySerializer],
        responses={200: ser.ProviderDepartureSerializer(many=True)},
        summary="An activity's departure calendar",
        description=(
            "§26.5's month grid: every departure in the window with its "
            "capacity, what is held, what is sold and any price override.\n\n"
            "Unlike the public calendar this publishes the three counters "
            "separately, because the split is what an operator is deciding "
            "on — eight taken seats of which four are holds is a boat being "
            "booked, not a boat that is full."
        ),
        tags=["inventory"],
    ),
    put=extend_schema(
        request=ser.DepartureEditSerializer,
        responses={200: None},
        summary="Bulk-edit an activity's departures",
        description=(
            "§26.5's bulk edit: a date range, an optional weekday mask, and "
            "set-availability / set-rate / open / close in one submission.\n\n"
            "**BR-023.** A capacity below what a departure has already held or "
            "sold is refused with `409 CAPACITY_BELOW_COMMITTED`, and every "
            "offending date is named in `details` with what was asked for and "
            "what is committed. Held seats count: a hold is a seat somebody is "
            "partway through paying for.\n\n"
            "Reducing capacity does not cancel anybody, and cancelling does "
            "not release anybody's money — §14.6's refund path is separate and "
            "deliberate."
        ),
        tags=["inventory"],
    ),
)
class AdminActivityDeparturesView(APIView):
    """§26.5's calendar, read and written.

    `PUT` rather than `PATCH`, because §26.5's submission is not a partial
    update of one row: it names a window and asserts what every departure in it
    should say. The body is idempotent — running it twice sets the same values —
    which is what makes an operator's retry safe after a timeout.
    """

    permission_classes = CATALOGUE_ADMIN_PERMISSIONS

    # Deliberately **not** a `ScopedQuerysetMixin` view, and the §37.2 matrix
    # records the route with that reason rather than this class declaring an
    # `ownership_resource` it does not apply.
    #
    # Every role that can reach this endpoint holds `CATALOGUE_MANAGE`, and
    # `OWNERSHIP` gives CATALOGUE_ADMIN and SUPER_ADMIN `Scope.GLOBAL` over
    # `ACTIVITY_DEPARTURE`. There is no predicate to apply: the filter would
    # provably match every row while reporting to the matrix that ownership was
    # enforced — which is what `catalogue.views._AdminCreateView` says about
    # the same temptation, and it is worse here, because the day a
    # provider-scoped role does reach this route the decoration would look like
    # the control that was already in place.
    #
    # Phase 11's provider route is where the predicate becomes real:
    # `_provider_listed(Resource.ACTIVITY_DEPARTURE, "activity__provider_id")`
    # is already written and already total over `Role x Resource`. What it
    # needs is a principal that can own an activity (ADR 0022), and a filtered
    # activity lookup to go with it.

    def get(self, request: Request, public_id: UUID) -> Response:
        query = ser.DepartureQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        window = dict(query.validated_data)

        listing, place = self._activity(public_id)
        today = timezone.localdate()
        since = window.get("date_from") or today
        until = window.get("to") or since + timedelta(days=DEFAULT_WINDOW_DAYS)

        rows = services.provider_calendar(
            listing,
            since=_start_of(since, place.timezone),
            until=_end_of(until, place.timezone),
        )
        return Response(
            success_envelope(
                [dict(ser.ProviderDepartureSerializer(row).data) for row in rows],
                {"timezone": place.timezone},
            )
        )

    def put(self, request: Request, public_id: UUID) -> Response:
        payload = ser.DepartureEditSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        edit = dict(payload.validated_data)

        listing, place = self._activity(public_id)
        changed = services.edit_departures(
            listing,
            DepartureEdit(
                since=edit["date_from"],
                until=edit["to"],
                weekday_mask=edit.get("weekday_mask"),
                capacity_total=edit.get("capacity_total"),
                price_override=edit.get("price_override"),
                clear_price=edit.get("clear_price", False),
                status=edit.get("status"),
            ),
            timezone_name=place.timezone,
        )
        return Response(success_envelope({"departures_changed": changed}))

    def _activity(self, public_id: UUID) -> tuple[int, Any]:
        """The activity's storage id and its destination's zone, or a 404.

        Resolved through `catalogue.services` without the visibility filter the
        public view applies: an operator edits the calendar of an activity that
        is withdrawn or whose market has not launched — publishing a season is
        exactly the case where the departures exist before the listing does.
        `resolve_curated_listing` is that accessor, and it says why.
        """
        listing = catalogue.resolve_curated_listing("activity", public_id)
        if listing is None:
            raise NotFoundError()
        place = catalogue.place_facts("activity", [listing.storage_id])[listing.storage_id]
        return listing.storage_id, place
