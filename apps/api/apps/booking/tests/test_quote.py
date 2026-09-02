"""`POST /trips/{id}/quote` — SRS §9.4.5, TC-050 to TC-052.

§9.4.5 calls this *"the most consequential endpoint in the system"*, and the
reason is the join: it is the only place an itinerary, a capacity counter and a
trip's state move together. Each half is tested where it lives —
`inventory/tests/test_services_hold.py` for the lock, `trip/tests` for the
costing — so what is asserted here is that they compose, in the order §9.4.5
gives, and that a failure anywhere leaves nothing behind.

The scenario builder reaches other modules' tables through `apps.get_model`,
never through their models: `private-trip` and `private-inventory` are not
relaxed for tests. Everything about *behaviour* goes through the two public
service surfaces.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services
from apps.common.errors import ConflictError, InventoryUnavailableError, NotFoundError
from apps.inventory import services as inventory
from apps.trip import services as trip_services

from . import scenario

pytestmark = pytest.mark.django_db


def _departure(scenario_: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(
        id=scenario_.departure_id
    )


def _trip(scenario_: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=scenario_.trip_id)


def _item(scenario_: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "ItineraryItem").objects.get(
        public_id=scenario_.item_public_id
    )


class TestTc050:
    """*"200; capacity_held incremented; quote_expires_at set."*"""

    def test_it_holds_capacity(self) -> None:
        case = scenario.build(adults=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _departure(case).capacity_held == 2

    def test_it_sets_the_quote_expiry(self) -> None:
        case = scenario.build()
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _trip(case).quote_expires_at == result.expires_at

    def test_the_trip_becomes_priced(self) -> None:
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _trip(case).status == "PRICED"

    def test_the_total_stops_being_zero(self) -> None:
        """What the whole phase is for. Two adults on a 95.00 activity."""
        case = scenario.build(adults=2, price_per_person="95.00")
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert str(result.trip.subtotal_amount) == "190.00"
        assert result.trip.total_amount > result.trip.subtotal_amount  # the platform fee

    def test_the_line_total_lands_on_the_item(self) -> None:
        case = scenario.build(adults=2, price_per_person="95.00")
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert str(_item(case).line_total) == "190.00"

    def test_the_departure_is_bound_to_the_item(self) -> None:
        """The column §7.5.11 has carried since Phase 4 with nothing to write
        it. `DEFERRED_INPUTS["VR-06"]` recorded exactly this gap."""
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _item(case).activity_departure_id == case.departure_id

    def test_children_take_seats_and_infants_do_not(self) -> None:
        """§10.7 prices per person; an infant does not occupy a seat. The
        party here must be the one `generate_itinerary` costs with, or a trip
        would have two subtotals."""
        case = scenario.build(adults=2, children=1)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _departure(case).capacity_held == 3

    def test_it_reports_how_many_seats_it_took(self) -> None:
        case = scenario.build(adults=2)
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert result.held_seats == 2


class TestTc051:
    """*"409 INVENTORY_UNAVAILABLE with alternatives; no counters changed."*"""

    def test_a_sold_out_departure_refuses_the_quote(self) -> None:
        case = scenario.build(adults=2, capacity=2, capacity_sold=2)
        with pytest.raises(InventoryUnavailableError):
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)

    def test_the_trip_is_not_priced_by_a_failed_quote(self) -> None:
        """The half that matters. A trip left PRICED with no holds behind it
        would offer a total nobody could honour."""
        case = scenario.build(adults=2, capacity=1, capacity_sold=1)
        with pytest.raises(InventoryUnavailableError):
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        trip = _trip(case)
        assert trip.status == "DRAFT"
        assert trip.priced_at is None
        assert trip.quote_expires_at is None

    def test_no_counter_moves(self) -> None:
        case = scenario.build(adults=2, capacity=1, capacity_sold=1)
        with pytest.raises(InventoryUnavailableError):
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _departure(case).capacity_held == 0

    def test_the_failure_names_the_departure_and_why(self) -> None:
        case = scenario.build(adults=2, capacity=1, capacity_sold=1)
        with pytest.raises(InventoryUnavailableError) as raised:
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert raised.value.details[0]["reason"] == "SOLD_OUT"

    def test_a_cancelled_departure_refuses_the_quote(self) -> None:
        case = scenario.build(departure_status="CANCELLED")
        with pytest.raises(InventoryUnavailableError):
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)


class TestWhatMayBeQuoted:
    """§9.4.5 step 1, and the two states it excludes."""

    def test_an_unplanned_trip_is_refused(self) -> None:
        """A quote holds capacity against a sequenced itinerary. Quoting one
        that was never planned would hold seats for a plan that does not
        exist."""
        case = scenario.build(generated=False)
        with pytest.raises(ConflictError) as raised:
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert raised.value.code == "TRIP_NOT_QUOTABLE"

    def test_an_itinerary_with_errors_is_refused(self) -> None:
        """§24.20: blocking errors disable Continue. §10.6 computed this once;
        recounting findings here would be a second quote gate."""
        case = scenario.build(validation_state="ERRORS")
        with pytest.raises(ConflictError) as raised:
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert raised.value.code == "TRIP_NOT_QUOTABLE"
        assert _departure(case).capacity_held == 0

    def test_an_activity_at_a_time_no_departure_leaves_is_refused(self) -> None:
        """Silently pricing it would sell a seat on a boat that is not
        running — the exact failure every layer beneath this prevents."""
        case = scenario.build()
        item = _item(case)
        item.starts_at = case.departs_at + dt.timedelta(minutes=30)
        item.save(update_fields=["starts_at"])

        with pytest.raises(ConflictError) as raised:
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert raised.value.code == "TRIP_NOT_QUOTABLE"

    def test_a_cancelled_trip_cannot_be_quoted(self) -> None:
        case = scenario.build()
        trip_services.cancel_trip(case.trip_public_id, tourist_id=case.tourist_id)
        with pytest.raises(ConflictError):
            services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)


class TestOwnership:
    def test_a_stranger_gets_404_not_403(self) -> None:
        """§30.3, inherited from `trip.services.quote_basis` rather than
        reimplemented: ownership is a filter, so there is no branch that could
        answer 403."""
        case = scenario.build()
        other = scenario.build()
        with pytest.raises(NotFoundError):
            services.quote_trip(case.trip_public_id, tourist_id=other.tourist_id)

    def test_a_stranger_takes_no_capacity(self) -> None:
        case = scenario.build()
        other = scenario.build()
        with pytest.raises(NotFoundError):
            services.quote_trip(case.trip_public_id, tourist_id=other.tourist_id)
        assert _departure(case).capacity_held == 0


class TestReQuoting:
    def test_a_second_quote_does_not_double_the_hold(self) -> None:
        """§9.4.5 step 2. Without the release a trip competes with itself for
        the last seats — by the tourist's own hand."""
        case = scenario.build(adults=2, capacity=3)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert _departure(case).capacity_held == 2

    def test_a_priced_trip_may_be_quoted_again(self) -> None:
        """§9.4.5: "assert trip.status in {DRAFT, PRICED}". §20.5 draws no
        self-loop, so this is a reprice in place rather than a transition."""
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        again = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert again.trip.status == "PRICED"

    def test_the_token_changes_between_quotes(self) -> None:
        """§9.4.6 must be able to refuse a token from a superseded quote."""
        case = scenario.build()
        first = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        second = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert first.quote_token != second.quote_token

    def test_the_expiry_moves_forward(self) -> None:
        case = scenario.build()
        first = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        second = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert second.expires_at >= first.expires_at


class TestWhatHoldsNothing:
    """ADR 0013 and §12.4, in the quote."""

    def test_a_stay_holds_nothing_and_prices_nothing(self) -> None:
        case = scenario.build(adults=2, price_per_person="95.00")
        scenario.add_stay(case)
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        # Only the activity contributed.
        assert str(result.trip.subtotal_amount) == "190.00"

    def test_a_trip_with_no_activities_still_quotes(self) -> None:
        """A tourist who has planned stays and attractions has a complete,
        correct itinerary that costs nothing — and asking for a price must
        tell them so rather than failing."""
        case = scenario.build(with_activity=False)
        scenario.add_stay(case)
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert result.held_seats == 0
        assert str(result.trip.subtotal_amount) == "0.00"
        assert result.trip.status == "PRICED"


class TestTc052:
    """*"Hold expires; sweeper runs; counters decremented; trip returns to
    DRAFT-equivalent."* The sweeper's own half is `inventory`'s; this is the
    trip half, and the join."""

    def test_the_expired_quote_returns_the_trip_to_draft(self) -> None:
        case = scenario.build()
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)

        touched = inventory.release_expired(now=result.expires_at + dt.timedelta(seconds=1))
        assert touched == [case.trip_id]
        assert trip_services.expire_quote(case.trip_id) is True

        trip = _trip(case)
        assert trip.status == "DRAFT"
        assert trip.quote_expires_at is None
        assert _departure(case).capacity_held == 0

    def test_the_totals_survive_the_expiry(self) -> None:
        """Only the offer expired, not the arithmetic. Blanking the figures
        would leave a tourist who walked away for half an hour looking at a
        plan that had apparently lost its price."""
        case = scenario.build(adults=2, price_per_person="95.00")
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        trip_services.expire_quote(case.trip_id)
        assert str(_trip(case).subtotal_amount) == "190.00"

    def test_expiring_a_draft_trip_changes_nothing(self) -> None:
        """Idempotent, and safe against a sweeper that arrives late."""
        case = scenario.build()
        assert trip_services.expire_quote(case.trip_id) is False

    def test_an_expired_trip_can_be_quoted_again(self) -> None:
        """The loop a tourist actually experiences: walk away, come back,
        press the button again."""
        case = scenario.build(adults=2, capacity=2)
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        inventory.release_expired(now=result.expires_at + dt.timedelta(seconds=1))
        trip_services.expire_quote(case.trip_id)

        again = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert again.trip.status == "PRICED"
        assert _departure(case).capacity_held == 2


class TestTheTtlIsConfigured:
    def test_the_hold_lasts_the_configured_window(self) -> None:
        """NFR-M07 and §17.2: twenty minutes is `quote.ttl_minutes`, not a
        number in a function."""
        from apps.common.config import get_setting

        case = scenario.build()
        before = timezone.now()
        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        minutes = int(get_setting("quote.ttl_minutes"))
        assert result.expires_at >= before + dt.timedelta(minutes=minutes - 1)
        assert result.expires_at <= timezone.now() + dt.timedelta(minutes=minutes)
