"""A basket with a transfer in it, against the committed seed — §9.4.6, ADR 0025.

The unit tests in `apps/booking/tests/test_basket.py` build one activity and
nothing else. A transfer takes a different branch: no listing, a seller found by
region (ADR 0025's addendum), a policy taken from a setting, and a
`booking_transfer` row freezing the corridor that priced it (BR-054). None of
that is reachable without a real itinerary whose planner inserted a leg, so this
builds one the way a tourist does — stay, two activities in two destinations,
plan, quote, confirm — on the seeded catalogue rather than on invented rows.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.utils import timezone

from apps.booking import services as booking
from apps.booking.models import Booking, BookingTransfer
from apps.catalogue import services as catalogue
from apps.inventory import services as inventory
from apps.trip import services as trip

pytestmark = pytest.mark.django_db

ZANZIBAR = ZoneInfo("Africa/Dar_es_Salaam")


def _tourist() -> int:
    user = django_apps.get_model("identity", "User").objects.create(
        email="basket-transfer@example.test", password="!unusable"
    )
    profile = django_apps.get_model("identity", "TouristProfile").objects.create(
        user=user, first_name="Ada", last_name="Lovelace"
    )
    return int(profile.id)


def _departure_on(slug: str, day: dt.date, today: dt.date) -> tuple[dt.datetime, int]:
    ref = catalogue.resolve_listing_ref("activity", slug, today=today)
    assert ref is not None, slug
    since = dt.datetime.combine(day, dt.time(0), tzinfo=ZANZIBAR)
    [first, *_] = inventory.list_departures(
        ref.storage_id, since=since, until=since + dt.timedelta(days=1), now=timezone.now()
    )
    duration = catalogue.activity_facts([ref.storage_id])[ref.storage_id].duration_minutes
    return first.departs_at, duration


@pytest.fixture
def basket_with_a_leg() -> tuple[int, object]:
    call_command("seed", verbosity=0)
    today = timezone.localdate()
    inventory.materialise_departures(start=today, horizon_days=40)

    tourist = _tourist()
    start = today + dt.timedelta(days=21)
    made = trip.create_trip(
        tourist_id=tourist,
        destination="stone-town",
        start_date=start,
        end_date=start + dt.timedelta(days=2),
        adults=2,
        today=today,
    )
    trip.add_item(
        made.public_id,
        tourist_id=tourist,
        today=today,
        item_type="STAY",
        day_number=1,
        sequence_no=1,
        starts_at=dt.datetime.combine(start, dt.time(14), tzinfo=ZANZIBAR),
        ends_at=dt.datetime.combine(start + dt.timedelta(days=2), dt.time(10), tzinfo=ZANZIBAR),
        accommodation="zanzibar-serena",
    )
    # Day two: the stay anchors the morning in Stone Town and the cruise sails
    # from Nungwi, so the planner inserts a leg across a seeded corridor.
    departs, minutes = _departure_on("sunset-dhow-cruise", start + dt.timedelta(days=1), today)
    trip.add_item(
        made.public_id,
        tourist_id=tourist,
        today=today,
        item_type="ACTIVITY",
        day_number=2,
        sequence_no=2,
        starts_at=departs,
        ends_at=departs + dt.timedelta(minutes=minutes),
        activity="sunset-dhow-cruise",
    )
    planned = trip.generate_itinerary(made.public_id, tourist_id=tourist)
    quote = booking.quote_trip(made.public_id, tourist_id=tourist)
    result = booking.create_basket(
        made.public_id, tourist_id=tourist, quote_token=quote.quote_token
    )
    return tourist, (planned, result)


class TestATransferBooking:
    def test_the_leg_becomes_a_transfer_booking(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        types = sorted(Booking.objects.values_list("booking_type", flat=True))
        assert types == ["ACTIVITY", "TRANSFER"]

    def test_it_freezes_the_corridor_that_priced_it(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        """BR-054 and ADR 0023: exactly one rule, and here it is a corridor."""
        leg = BookingTransfer.objects.get()
        assert leg.corridor_id is not None and leg.tariff_id is None
        corridor = django_apps.get_model("transport", "TransferCorridor").objects.get(
            id=leg.corridor_id
        )
        assert leg.booking.gross_amount == corridor.fixed_price
        assert leg.vehicle_class == "STANDARD"

    def test_it_keeps_the_quality_of_the_distance_it_was_priced_beside(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        """ADR 0019: an approximate distance is stored as approximate, not laundered."""
        leg = BookingTransfer.objects.get()
        assert leg.estimate_quality == "APPROXIMATE"

    def test_it_is_sold_by_the_transport_provider_of_its_origin_region(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        """ADR 0025's addendum. Stone Town is Zanzibar Urban/West."""
        leg = Booking.objects.get(booking_type="TRANSFER")
        seller = django_apps.get_model("provider", "Provider").objects.get(id=leg.provider_id)
        assert (seller.provider_type, seller.trading_name) == ("TRANSPORT", "Urban West Transfers")

    def test_it_snapshots_the_platform_s_transfer_policy(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        leg = Booking.objects.get(booking_type="TRANSFER")
        assert leg.cancellation_policy_id is None
        assert leg.cancellation_policy_snapshot["code"] == "FLEX_48H"

    def test_the_activity_is_sold_by_its_seeded_operator(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        cruise = Booking.objects.get(booking_type="ACTIVITY")
        seller = django_apps.get_model("provider", "Provider").objects.get(id=cruise.provider_id)
        assert seller.trading_name == "North Coast Reef Excursions"

    def test_the_fee_shares_sum_to_the_trip_fee(
        self, basket_with_a_leg: tuple[int, object]
    ) -> None:
        trip_row = django_apps.get_model("trip", "Trip").objects.get()
        assert sum(b.fee_amount for b in Booking.objects.all()) == trip_row.fee_amount
        assert sum(b.gross_amount for b in Booking.objects.all()) == trip_row.subtotal_amount
