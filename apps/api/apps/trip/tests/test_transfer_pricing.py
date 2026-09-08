"""A planned transfer costs money — SRS §10.7, §12.4, §12.6, §9.4.5.

Phase 4 left every transfer unpriced and said so in `_priced`'s own docstring.
This is where that stops being true, and where ADR 0019's line is drawn in
code rather than in prose:

* **Planning tolerates a leg it cannot price.** A tourist arranging days must
  not be blocked because a fare is unavailable, so an unpriceable transfer is
  written with no money on it and the generate succeeds.
* **Quoting does not.** §9.4.5 is where the platform commits to a number, and a
  total with a leg silently missing from it would be a commitment to a price
  nobody computed.

The transport *rows* are reached through `django_apps.get_model` rather than
by importing them: `.importlinter` forbids `trip -> apps.transport.models` and
the contract is not relaxed for tests, which is the same reason `external_rows`
exists. `apps.transport.services` **is** imported, because that is the module's
declared public door and `trip -> transport` is a permitted edge — the errors
it raises are part of that interface and asserting on their type rather than on
their wording is what keeps this test about behaviour.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps

from apps.transport import services as transport_services
from apps.trip import services
from apps.trip.models import ItemType
from apps.trip.tests import external_rows

pytestmark = pytest.mark.django_db

START = dt.date(2027, 6, 1)
END = dt.date(2027, 6, 4)


def at(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2027, 6, day, hour, tzinfo=dt.UTC) - dt.timedelta(hours=12)


def _transport(name: str) -> Any:
    return django_apps.get_model("transport", name)


def _classes() -> dict[str, Any]:
    model = _transport("VehicleClass")
    spec = [("STANDARD", 4, 3, 1), ("VAN", 7, 6, 2)]
    return {
        code: model.objects.create(
            code=code, name=code.title(), seats=seats, luggage_capacity=bags, display_order=order
        )
        for code, seats, bags, order in spec
    }


def _corridor(klass: Any, origin: Any, target: Any, price: str = "90000.00") -> Any:
    return _transport("TransferCorridor").objects.create(
        origin_destination_id=origin.pk,
        target_destination_id=target.pk,
        vehicle_class=klass,
        fixed_price=Decimal(price),
        currency=origin.default_currency,
        valid_from=dt.date(2026, 1, 1),
    )


def _tariff(klass: Any, region_id: int, currency: str) -> Any:
    return _transport("TransferTariff").objects.create(
        scope="REGION",
        region_id=region_id,
        vehicle_class=klass,
        base_fare=Decimal("30000.00"),
        per_km_rate=Decimal("1620.0000"),
        currency=currency,
        valid_from=dt.date(2026, 1, 1),
    )


class Journey:
    """A stay in one town and an activity in another, so §10.4 inserts a leg
    that crosses a corridor. Two destinations rather than one is the whole
    setup: a transfer within a town has both ends in the same place, which is
    ordinary and which no corridor prices."""

    def __init__(self) -> None:
        self.home = external_rows.make_destination(longitude=174.05)
        self.away = external_rows.make_destination(longitude=175.60)
        self.tourist = external_rows.make_tourist_id()
        self.trip = services.create_trip(
            tourist_id=self.tourist,
            destination=self.home.slug,
            start_date=START,
            end_date=END,
            adults=2,
            today=START - dt.timedelta(days=30),
        )
        self.accommodation = external_rows.make_accommodation(self.home)
        self.activity = external_rows.make_activity(self.away)

        services.add_item(
            self.trip.public_id,
            tourist_id=self.tourist,
            item_type=ItemType.STAY,
            day_number=1,
            sequence_no=1,
            title="Harbourside Lodge",
            starts_at=at(1, 14),
            ends_at=at(4, 10),
            accommodation=self.accommodation.slug,
        )
        services.add_item(
            self.trip.public_id,
            tourist_id=self.tourist,
            item_type=ItemType.ACTIVITY,
            day_number=1,
            sequence_no=2,
            title="Harbour Kayak Tour",
            starts_at=at(1, 16),
            ends_at=at(1, 19),
            activity=self.activity.slug,
        )

    def generate(self) -> Any:
        return services.generate_itinerary(self.trip.public_id, tourist_id=self.tourist)

    def quote(self) -> Any:
        """§9.4.5 steps 6-8, without the inventory half.

        `departures` is empty because nothing here holds a seat: the point of
        this fixture is the transfer, and binding a departure would drag
        `inventory` into a test about tariffs.
        """
        return services.mark_priced(
            self.trip.public_id,
            tourist_id=self.tourist,
            departures={},
            expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=20),
        )


@pytest.fixture
def journey() -> Journey:
    return Journey()


def _transfers(result: Any) -> list[Any]:
    return [i for i in result.itinerary.items if i.item_type == ItemType.TRANSFER]


class TestAPlannedTransferIsPriced:
    def test_a_corridor_puts_money_on_the_leg(self, journey: Journey) -> None:
        """§10.7's TRANSFER arm, which did not exist before Phase 6."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)

        (transfer, *_) = _transfers(journey.generate())
        assert transfer.line_total == Decimal("90000.00")
        assert transfer.currency == journey.home.default_currency

    def test_the_subtotal_includes_it(self, journey: Journey) -> None:
        """The number a tourist actually reads. A line total written onto a row
        that never reached the subtotal would satisfy the assertion above and
        change nothing on screen."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)

        without = Decimal(str(journey.generate().subtotal_amount))
        _transport("TransferCorridor").all_objects.all().delete()
        with_nothing = Decimal(str(journey.generate().subtotal_amount))

        assert without - with_nothing == Decimal("90000.00")

    def test_a_transfer_is_one_vehicle_not_one_per_passenger(self, journey: Journey) -> None:
        """The party is two. Charging per head would make this 180,000 and
        would look plausible next to an activity priced per person."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)
        (transfer, *_) = _transfers(journey.generate())
        assert transfer.line_total == Decimal("90000.00")

    def test_the_class_and_luggage_are_stored_on_the_leg(self, journey: Journey) -> None:
        """§12.2: the binding is stored "so that the leg can be re-priced
        identically later"."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)
        (transfer, *_) = _transfers(journey.generate())
        assert transfer.vehicle_class == "STANDARD"
        assert transfer.luggage_count is not None

    def test_the_cheapest_fitting_class_is_chosen(self, journey: Journey) -> None:
        """ADR 0023 decision 5. A starting point rather than an answer — §24.17
        lets the tourist change it — but the starting point must not be the
        most expensive vehicle on the lot."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away, "90000.00")
        _corridor(classes["VAN"], journey.home, journey.away, "130000.00")
        (transfer, *_) = _transfers(journey.generate())
        assert transfer.vehicle_class == "STANDARD"
        assert transfer.line_total == Decimal("90000.00")

    def test_the_endpoints_are_recorded(self, journey: Journey) -> None:
        """§7.5.11's `origin_destination_id`/`target_destination_id`, which no
        inserted transfer carried before Phase 6. Without them a re-quote has
        no idea which corridor applied."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)
        (transfer, *_) = _transfers(journey.generate())
        assert transfer.origin_destination is not None
        assert transfer.target_destination is not None


class TestPlanningToleratesAnUnpriceableLeg:
    """ADR 0019: planning may estimate, quoting may not."""

    def test_no_tariff_leaves_the_leg_unpriced_and_the_plan_intact(self, journey: Journey) -> None:
        _classes()
        result = journey.generate()
        (transfer, *_) = _transfers(result)
        assert transfer.line_total is None
        assert result.itinerary.version >= 1

    def test_the_metered_fallback_does_not_price_an_estimated_leg(self, journey: Journey) -> None:
        """§12.6. Every distance is a haversine estimate until D-2, so a leg
        with only a metered tariff has no measured road to charge for — and
        the plan still succeeds, with no money on that leg."""
        classes = _classes()
        _tariff(classes["STANDARD"], journey.home.region_id, journey.home.default_currency)

        (transfer, *_) = _transfers(journey.generate())
        assert transfer.estimate_quality == "APPROXIMATE"
        assert transfer.line_total is None

    def test_an_unpriced_leg_still_names_a_class(self, journey: Journey) -> None:
        """The database requires one of every transfer, and §24.17 offers the
        tourist a different one — which is how an unpriceable leg becomes a
        priceable one."""
        _classes()
        (transfer, *_) = _transfers(journey.generate())
        assert transfer.vehicle_class == "STANDARD"


class TestQuotingReQuotes:
    def test_the_quote_uses_the_tariff_in_force_now(self, journey: Journey) -> None:
        """§9.4.5: "for each transfer item: re-quote using current tariffs".

        Not the stored line total. The generate wrote one price; the corridor
        then changed, and the quote must commit to the new one — otherwise a
        tourist pays yesterday's fare and the platform absorbs the difference
        forever.
        """
        classes = _classes()
        corridor = _corridor(classes["STANDARD"], journey.home, journey.away, "90000.00")
        planned = Decimal(str(journey.generate().subtotal_amount))

        corridor.fixed_price = Decimal("120000.00")
        corridor.save(update_fields=["fixed_price"])

        quoted = Decimal(str(journey.quote().subtotal_amount))
        assert quoted - planned == Decimal("30000.00")

    def test_a_quote_refuses_a_leg_it_cannot_price(self, journey: Journey) -> None:
        """The other half of ADR 0019's line. A total that silently omitted a
        leg would be a commitment to a price nobody computed."""
        classes = _classes()
        _corridor(classes["STANDARD"], journey.home, journey.away)
        journey.generate()

        _transport("TransferCorridor").all_objects.all().delete()
        _tariff(classes["STANDARD"], journey.home.region_id, journey.home.default_currency)

        with pytest.raises(transport_services.RoutingUnavailableError) as caught:
            journey.quote()
        assert caught.value.status_code == 502

    def test_a_quote_with_no_transfer_at_all_is_unaffected(self) -> None:
        """The control: the strictness must not turn every trip without a
        corridor into a failure. A trip whose items share one destination has
        no leg to price."""
        destination = external_rows.make_destination()
        tourist = external_rows.make_tourist_id()
        trip = services.create_trip(
            tourist_id=tourist,
            destination=destination.slug,
            start_date=START,
            end_date=END,
            adults=2,
            today=START - dt.timedelta(days=30),
        )
        services.add_item(
            trip.public_id,
            tourist_id=tourist,
            item_type=ItemType.ACTIVITY,
            day_number=1,
            sequence_no=1,
            title="Harbour Kayak Tour",
            starts_at=at(1, 16),
            ends_at=at(1, 19),
            activity=external_rows.make_activity(destination).slug,
        )
        services.generate_itinerary(trip.public_id, tourist_id=tourist)
        assert (
            services.mark_priced(
                trip.public_id,
                tourist_id=tourist,
                departures={},
                expires_at=dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=20),
            )
            is not None
        )
