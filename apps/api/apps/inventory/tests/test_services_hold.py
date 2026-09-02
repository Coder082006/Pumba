"""Holding capacity — SRS §17.3, §17.5, §20.8, BR-022 to BR-026.

The routine the oversell guarantee rests on, and the one place in the system
where being *nearly* right is indistinguishable from being right until a
tourist arrives at a beach with no seat on the boat.

TC-050 to TC-053 are all about this file's subject. The genuinely concurrent
half of TC-053 needs two live database connections and lives in
`test_concurrency.py`; everything a single transaction can prove is here.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.common.errors import InventoryUnavailableError, ValidationError
from apps.inventory import services
from apps.inventory.domain.capacity import Unbookable
from apps.inventory.dto import AvailabilityBasis, HoldRequest
from apps.inventory.models import ActivityDeparture, HoldStatus, InventoryHold
from apps.inventory.tests.factories import make_departure, set_activity_rules

pytestmark = pytest.mark.django_db

TTL = 20
TRIP = 1
OTHER_TRIP = 2


def _now() -> dt.datetime:
    return timezone.now()


def _held(departure: ActivityDeparture) -> int:
    departure.refresh_from_db()
    return departure.capacity_held


class TestHoldTakesCapacity:
    """TC-050: "200; capacity_held incremented"."""

    def test_a_hold_moves_the_counter(self) -> None:
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert _held(departure) == 3

    def test_it_writes_a_row_that_says_why(self) -> None:
        """§17.1 I4: an explicit row, not a status somebody infers."""
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=2)],
            ttl_minutes=TTL,
            now=_now(),
        )
        hold = InventoryHold.objects.get()
        assert hold.trip_id == TRIP
        assert hold.resource_id == departure.id
        assert hold.quantity == 2
        assert hold.status == HoldStatus.HELD

    def test_the_ttl_is_the_one_the_caller_supplied(self) -> None:
        """§17.2's twenty minutes is a `system_setting`, read by the caller.
        A default buried here would be a business constant in the domain."""
        departure = make_departure()
        now = _now()
        [held] = services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=45,
            now=now,
        )
        assert held.expires_at == now + dt.timedelta(minutes=45)

    def test_two_items_on_one_departure_are_one_claim_on_it(self) -> None:
        """Two adults on the morning snorkel and two children on the same
        boat. Locking the row twice and asserting separately would let a
        four-seat departure accept two twos."""
        departure = make_departure(capacity_total=4, capacity_sold=1)
        with pytest.raises(InventoryUnavailableError):
            services.hold(
                trip_id=TRIP,
                requests=[
                    HoldRequest(departure_id=departure.id, pax=2),
                    HoldRequest(departure_id=departure.id, pax=2),
                ],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert _held(departure) == 0

    def test_each_request_gets_its_own_hold_row(self) -> None:
        first = make_departure()
        second = make_departure(
            activity_id=first.activity_id, departs_at=first.departs_at + dt.timedelta(days=1)
        )
        held = services.hold(
            trip_id=TRIP,
            requests=[
                HoldRequest(departure_id=first.id, pax=2),
                HoldRequest(departure_id=second.id, pax=2),
            ],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert len(held) == 2
        assert InventoryHold.objects.count() == 2


class TestHoldRefusesWhatItCannotSell:
    """TC-051: "409 INVENTORY_UNAVAILABLE with alternatives; no counters
    changed"."""

    def test_a_sold_out_departure_is_refused(self) -> None:
        departure = make_departure(capacity_total=4, capacity_sold=4)
        with pytest.raises(InventoryUnavailableError):
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )

    def test_nothing_moves_when_the_hold_is_refused(self) -> None:
        """ "No counters changed" is the half of TC-051 that is easy to get
        wrong: a routine that incremented as it went would leave the first
        departure of a two-departure request held against a quote that failed."""
        good = make_departure(capacity_total=12)
        full = make_departure(
            activity_id=good.activity_id,
            departs_at=good.departs_at + dt.timedelta(days=1),
            capacity_total=2,
            capacity_sold=2,
        )
        with pytest.raises(InventoryUnavailableError):
            services.hold(
                trip_id=TRIP,
                requests=[
                    HoldRequest(departure_id=good.id, pax=1),
                    HoldRequest(departure_id=full.id, pax=1),
                ],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert _held(good) == 0
        assert InventoryHold.objects.count() == 0

    def test_every_unavailable_item_is_named_at_once(self) -> None:
        """§9.4.5's `details` is an array. A tourist told about one sold-out
        activity at a time, over three attempts, gives up before the third."""
        first = make_departure(capacity_total=1, capacity_sold=1)
        second = make_departure(
            activity_id=first.activity_id,
            departs_at=first.departs_at + dt.timedelta(days=1),
            capacity_total=1,
            capacity_sold=1,
        )
        with pytest.raises(InventoryUnavailableError) as raised:
            services.hold(
                trip_id=TRIP,
                requests=[
                    HoldRequest(departure_id=first.id, pax=1),
                    HoldRequest(departure_id=second.id, pax=1),
                ],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert len(raised.value.details) == 2

    def test_the_reason_is_carried(self) -> None:
        departure = make_departure(capacity_total=1, capacity_sold=1)
        with pytest.raises(InventoryUnavailableError) as raised:
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert raised.value.details[0]["reason"] == Unbookable.SOLD_OUT.value

    def test_alternatives_are_offered_where_they_exist(self) -> None:
        """§9.4.5: the 409 carries "alternative departures". Offering them is
        the difference between an error that ends a booking and one that
        redirects it."""
        full = make_departure(capacity_total=1, capacity_sold=1)
        make_departure(
            activity_id=full.activity_id, departs_at=full.departs_at + dt.timedelta(days=1)
        )
        with pytest.raises(InventoryUnavailableError) as raised:
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=full.id, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert raised.value.details[0]["alternatives"]

    def test_a_cancelled_departure_offers_alternatives_too(self) -> None:
        cancelled = make_departure(status="CANCELLED")
        make_departure(
            activity_id=cancelled.activity_id,
            departs_at=cancelled.departs_at + dt.timedelta(days=1),
        )
        with pytest.raises(InventoryUnavailableError) as raised:
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=cancelled.id, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert raised.value.details[0]["reason"] == Unbookable.CANCELLED.value
        assert raised.value.details[0]["alternatives"]

    def test_a_party_past_the_cutoff_is_refused(self) -> None:
        """BR-034, through the same path."""
        departure = make_departure(departs_at=timezone.now() + dt.timedelta(hours=1))
        with pytest.raises(InventoryUnavailableError) as raised:
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert raised.value.details[0]["reason"] == Unbookable.PAST_CUTOFF.value

    def test_a_hold_for_nobody_is_rejected(self) -> None:
        departure = make_departure()
        with pytest.raises(ValidationError):
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=departure.id, pax=0)],
                ttl_minutes=TTL,
                now=_now(),
            )

    def test_an_unknown_departure_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=999_999, pax=1)],
                ttl_minutes=TTL,
                now=_now(),
            )

    def test_holding_nothing_is_not_an_error(self) -> None:
        """A trip of stays and attractions quotes fine and holds nothing."""
        assert services.hold(trip_id=TRIP, requests=[], ttl_minutes=TTL, now=_now()) == []


class TestAReQuoteReleasesTheTripsOwnHolds:
    """§9.4.5 step 2, and the defect it prevents."""

    def test_the_prior_hold_is_released(self) -> None:
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert _held(departure) == 3

    def test_a_trip_does_not_compete_with_itself_for_the_last_seats(self) -> None:
        """The defect. Without the release, re-quoting an unchanged itinerary
        double-counts the trip's own seats against itself and the second quote
        fails as sold out — by the tourist's own hand."""
        departure = make_departure(capacity_total=4)
        for _ in range(3):
            services.hold(
                trip_id=TRIP,
                requests=[HoldRequest(departure_id=departure.id, pax=4)],
                ttl_minutes=TTL,
                now=_now(),
            )
        assert _held(departure) == 4

    def test_the_superseded_hold_reads_as_released_not_expired(self) -> None:
        """Something decided. §17.4's reconciliation is an investigation, and
        this is the distinction that makes it one."""
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert InventoryHold.objects.filter(status=HoldStatus.RELEASED).count() == 1

    def test_another_trip_s_holds_are_untouched(self) -> None:
        """The obvious catastrophe, asserted because it is one line of filter
        away."""
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=OTHER_TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=2)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert _held(departure) == 5


class TestRelease:
    def test_it_gives_the_capacity_back(self) -> None:
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=4)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert services.release(trip_id=TRIP) == 1
        assert _held(departure) == 0

    def test_releasing_twice_is_a_no_op(self) -> None:
        """§8.8 requires idempotence of every job, and the two callers most
        likely to repeat this are a retrying task and a tourist pressing a
        button twice."""
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.release(trip_id=TRIP)
        assert services.release(trip_id=TRIP) == 0
        assert _held(departure) == 0


class TestCommit:
    """§20.8 step 9, called by Phase 7's confirmation routine."""

    def test_it_moves_held_to_sold(self) -> None:
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.commit(trip_id=TRIP, now=_now())
        departure.refresh_from_db()
        assert departure.capacity_held == 0
        assert departure.capacity_sold == 3

    def test_the_total_spoken_for_does_not_change(self) -> None:
        """§17.2: the counters *move*. A commit that decremented held without
        incrementing sold would free a seat that had just been paid for."""
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.commit(trip_id=TRIP, now=_now())
        departure.refresh_from_db()
        assert departure.capacity_held + departure.capacity_sold == 3

    def test_the_hold_reads_as_committed(self) -> None:
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=_now(),
        )
        services.commit(trip_id=TRIP, now=_now())
        assert InventoryHold.objects.get().status == HoldStatus.COMMITTED

    def test_an_expired_hold_may_not_be_committed(self) -> None:
        """BR-026, and §17.1 I5's defensive re-check: a hold that expired
        while the payment was in flight must not sell a seat the sweeper had
        already given back."""
        departure = make_departure()
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=now,
        )
        with pytest.raises(InventoryUnavailableError) as raised:
            services.commit(trip_id=TRIP, now=now + dt.timedelta(minutes=TTL + 1))
        assert raised.value.code == "HOLD_EXPIRED"

    def test_committing_nothing_is_not_an_error(self) -> None:
        assert services.commit(trip_id=TRIP, now=_now()) == 0


class TestReleaseExpired:
    """§17.5's sweeper, over this module's own rows. TC-052."""

    def test_an_expired_hold_returns_its_capacity(self) -> None:
        departure = make_departure(capacity_total=12)
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=4)],
            ttl_minutes=TTL,
            now=now,
        )
        services.release_expired(now=now + dt.timedelta(minutes=TTL + 1))
        assert _held(departure) == 0

    def test_the_hold_reads_as_expired_not_released(self) -> None:
        """Nobody decided; the clock ran out."""
        departure = make_departure()
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=now,
        )
        services.release_expired(now=now + dt.timedelta(minutes=TTL + 1))
        assert InventoryHold.objects.get().status == HoldStatus.EXPIRED

    def test_it_names_the_trips_it_touched(self) -> None:
        """`inventory` may not move a trip back to DRAFT (§6.4, ADR 0022), so
        it reports rather than acts and `booking` joins the two halves."""
        departure = make_departure()
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=now,
        )
        assert services.release_expired(now=now + dt.timedelta(minutes=TTL + 1)) == [TRIP]

    def test_a_live_hold_is_left_alone(self) -> None:
        departure = make_departure()
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=2)],
            ttl_minutes=TTL,
            now=now,
        )
        assert services.release_expired(now=now + dt.timedelta(minutes=1)) == []
        assert _held(departure) == 2

    def test_sweeping_twice_returns_the_capacity_once(self) -> None:
        """§8.8: "Idempotent". A second pass that decremented again would
        oversell by exactly the amount it had already given back."""
        departure = make_departure(capacity_total=12, capacity_sold=2)
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=now,
        )
        later = now + dt.timedelta(minutes=TTL + 1)
        services.release_expired(now=later)
        services.release_expired(now=later)
        assert _held(departure) == 0

    def test_a_committed_hold_is_never_swept(self) -> None:
        """It is past its TTL and it is also paid for."""
        departure = make_departure()
        now = _now()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=1)],
            ttl_minutes=TTL,
            now=now,
        )
        services.commit(trip_id=TRIP, now=now)
        services.release_expired(now=now + dt.timedelta(minutes=TTL + 1))
        departure.refresh_from_db()
        assert departure.capacity_sold == 1
        assert InventoryHold.objects.get().status == HoldStatus.COMMITTED

    def test_the_batch_size_is_respected(self) -> None:
        """§17.5: "in batches of 200". A sweeper that took the whole backlog
        in one pass would hold locks for as long as the backlog was deep."""
        departure = make_departure(capacity_total=30)
        now = _now()
        for trip in range(5):
            services.hold(
                trip_id=trip + 10,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=now,
            )
        touched = services.release_expired(now=now + dt.timedelta(minutes=TTL + 1), limit=2)
        assert len(touched) == 2
        assert _held(departure) == 3


class TestCheckAvailability:
    """§6.4's `check_availability()` — indicative, no lock."""

    def test_a_bookable_departure_reports_no_reason(self) -> None:
        departure = make_departure()
        answer = services.check_availability(
            [HoldRequest(departure_id=departure.id, pax=2)], now=_now()
        )
        assert answer == {departure.id: None}

    def test_it_reports_why_not(self) -> None:
        departure = make_departure(capacity_total=1, capacity_sold=1)
        answer = services.check_availability(
            [HoldRequest(departure_id=departure.id, pax=1)], now=_now()
        )
        assert answer == {departure.id: Unbookable.SOLD_OUT}

    def test_it_takes_no_lock_and_changes_nothing(self) -> None:
        departure = make_departure()
        services.check_availability([HoldRequest(departure_id=departure.id, pax=2)], now=_now())
        assert _held(departure) == 0


class TestListDepartures:
    def test_it_answers_indicatively_and_says_so(self) -> None:
        """§17.1 I3, §8.10: a cached availability figure may never confirm a
        booking, and a number with no provenance invites a client to forget."""
        departure = make_departure()
        [dto] = services.list_departures(
            departure.activity_id,
            since=departure.departs_at - dt.timedelta(days=1),
            until=departure.departs_at + dt.timedelta(days=1),
            now=_now(),
        )
        assert dto.basis is AvailabilityBasis.INDICATIVE

    def test_remaining_subtracts_held_and_sold(self) -> None:
        departure = make_departure(capacity_total=12, capacity_held=2, capacity_sold=6)
        [dto] = services.list_departures(
            departure.activity_id,
            since=departure.departs_at - dt.timedelta(days=1),
            until=departure.departs_at + dt.timedelta(days=1),
            now=_now(),
        )
        assert dto.remaining == 4

    def test_a_cancelled_departure_is_shown_rather_than_hidden(self) -> None:
        """§24.10 shows a calendar. A date that silently vanishes reads as a
        bug to somebody who was looking at it a minute ago; a date marked
        cancelled reads as weather."""
        departure = make_departure(status="CANCELLED")
        dtos = services.list_departures(
            departure.activity_id,
            since=departure.departs_at - dt.timedelta(days=1),
            until=departure.departs_at + dt.timedelta(days=1),
            now=_now(),
        )
        assert [d.status for d in dtos] == ["CANCELLED"]

    def test_a_party_size_turns_the_list_into_advice(self) -> None:
        departure = make_departure(capacity_total=4)
        set_activity_rules(departure.activity_id, max_pax=4)
        [dto] = services.list_departures(
            departure.activity_id,
            since=departure.departs_at - dt.timedelta(days=1),
            until=departure.departs_at + dt.timedelta(days=1),
            now=_now(),
            pax=6,
        )
        assert dto.unbookable is Unbookable.PARTY_TOO_LARGE
        assert not dto.is_bookable

    def test_without_a_party_size_no_judgement_is_offered(self) -> None:
        departure = make_departure()
        [dto] = services.list_departures(
            departure.activity_id,
            since=departure.departs_at - dt.timedelta(days=1),
            until=departure.departs_at + dt.timedelta(days=1),
            now=_now(),
        )
        assert dto.unbookable is None


class TestResolveDepartureAt:
    """How `booking` binds a departure without `trip` reaching into here."""

    def test_an_exact_instant_finds_its_departure(self) -> None:
        departure = make_departure()
        found = services.resolve_departure_at(
            departure.activity_id, departs_at=departure.departs_at
        )
        assert found == departure.id

    def test_a_time_no_departure_leaves_at_finds_nothing(self) -> None:
        departure = make_departure()
        found = services.resolve_departure_at(
            departure.activity_id, departs_at=departure.departs_at + dt.timedelta(minutes=1)
        )
        assert found is None


class TestReconcile:
    """§17.4, over the half of the system that exists (ADR 0022)."""

    def test_a_healthy_counter_reports_no_drift(self) -> None:
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=TTL,
            now=_now(),
        )
        assert services.reconcile() == []

    def test_a_counter_with_no_holds_behind_it_is_drift(self) -> None:
        """The failure this job exists to catch: capacity spoken for by
        nothing, which no tourist can buy and no sweeper will ever return."""
        departure = make_departure(capacity_held=4)
        [drift] = services.reconcile()
        assert drift.departure_public_id == departure.public_id
        assert drift.capacity_held == 4
        assert drift.held_by_live_holds == 0

    def test_both_numbers_are_reported_rather_than_their_difference(self) -> None:
        """Which way a counter drifted says which half of the system to look
        at, and a signed delta is one sign error away from saying the
        opposite."""
        make_departure(capacity_held=2)
        [drift] = services.reconcile()
        assert (drift.capacity_held, drift.held_by_live_holds) == (2, 0)

    def test_an_untouched_departure_is_not_examined(self) -> None:
        make_departure()
        assert services.reconcile() == []

    def test_a_released_hold_no_longer_justifies_capacity(self) -> None:
        departure = make_departure()
        services.hold(
            trip_id=TRIP,
            requests=[HoldRequest(departure_id=departure.id, pax=2)],
            ttl_minutes=TTL,
            now=_now(),
        )
        # Move the counter without the hold, which is what a partial failure
        # would leave behind.
        ActivityDeparture.objects.filter(id=departure.id).update(capacity_held=5)
        [drift] = services.reconcile()
        assert (drift.capacity_held, drift.held_by_live_holds) == (5, 2)
