"""VR-01 to VR-17 — SRS §10.6.

One class per rule, and every rule gets both a passing and a failing case. A
validation suite that only tests failures is a suite that would pass if every
rule fired on everything, and a banner that is always red is a banner nobody
reads.

Three of the rules are not what §10.6's first table suggests, and the SRS's own
v1.2 amendments say so. Those three — VR-04, VR-05, VR-11 — are tested against
the amended text, with the reason written into each test, because they are the
ones somebody will later "fix" back to the original reading.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from apps.trip.domain.findings import Severity
from apps.trip.domain.sequencing import Buffers, Kind
from apps.trip.domain.validation import (
    ALL_RULES,
    DEFERRED_RULES,
    FlightFacts,
    ItemFacts,
    Limits,
    PartyFacts,
    TripFacts,
    validate,
)

ZONE = "Pacific/Auckland"
START = date(2027, 6, 1)
END = date(2027, 6, 6)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant in UTC on a given trip day.

    Auckland is UTC+12 in June, so 21:00 UTC on the previous day is 09:00
    local. Rather than reason about that in every test, the trip's own
    timezone is only exercised by the tests that are about it.
    """
    return datetime(2027, 6, day, hour, minute, tzinfo=UTC)


def trip_facts(**overrides: object) -> TripFacts:
    values: dict[str, object] = {
        "start_date": START,
        "end_date": END,
        "timezone": ZONE,
        "currency": "NZD",
        "party": PartyFacts(adults=2),
    }
    values.update(overrides)
    return TripFacts(**values)  # type: ignore[arg-type]


def item(
    item_id: int = 1,
    kind: Kind = Kind.ACTIVITY,
    *,
    day: int = 1,
    starts: datetime | None = None,
    ends: datetime | None = None,
    **overrides: object,
) -> ItemFacts:
    values: dict[str, object] = {
        "item_id": item_id,
        "kind": kind,
        "day_number": day,
        "title": f"item-{item_id}",
        "starts_at": starts if starts is not None else at(1, 9),
        "ends_at": ends if ends is not None else at(1, 10),
    }
    values.update(overrides)
    return ItemFacts(**values)  # type: ignore[arg-type]


def run(items: list[ItemFacts], **overrides: object) -> tuple[str, ...]:
    """Every rule, returning just the codes that fired."""
    findings = validate(
        items,
        trip=overrides.pop("trip", None) or trip_facts(),  # type: ignore[arg-type]
        buffers=overrides.pop("buffers", None) or Buffers(),  # type: ignore[arg-type]
        limits=overrides.pop("limits", None) or Limits(),  # type: ignore[arg-type]
    )
    return tuple(f.code for f in findings)


def only(items: list[ItemFacts], code: str, **overrides: object) -> tuple[str, ...]:
    """The codes for one rule, ignoring anything else that also fired.

    Necessary because most fixtures trip VR-16 — a trip with no stay anchor
    has uncovered nights — and that is correct behaviour rather than noise to
    be suppressed.
    """
    return tuple(c for c in run(items, **overrides) if c == code)


class TestTheRuleSet:
    def test_the_implemented_rules_are_exactly_these(self) -> None:
        """Sixteen of the seventeen. Stated as a set so a rule quietly
        vanishing from the chain fails here rather than passing silently."""
        assert len(ALL_RULES) == 16
        assert "VR-11" not in ALL_RULES

    def test_every_missing_rule_is_a_declared_deferral(self) -> None:
        expected = {f"VR-{n:02d}" for n in range(1, 18)}
        assert ALL_RULES | set(DEFERRED_RULES) == expected

    def test_each_deferral_states_its_reason(self) -> None:
        """A list of absent rules with no reasons decays into a list nobody
        trusts — the same argument as the port registry's."""
        for code, reason in DEFERRED_RULES.items():
            assert "ADR 0013" in reason, f"{code} must name the decision that defers it"
            assert len(reason) > 40

    def test_a_clean_itinerary_produces_nothing(self) -> None:
        """The other half of every test below. Without this, a rule set that
        fired on everything would pass the failure cases perfectly."""
        stay = item(
            1,
            Kind.STAY_CHECK_IN,
            starts=at(1, 14),
            ends=at(1, 14),
            covered_nights=tuple(trip_facts().nights),
        )
        assert run([stay]) == ()


class TestVR01WithinTheTripWindow:
    def test_an_item_inside_the_dates_passes(self) -> None:
        assert only([item(starts=at(2, 9), ends=at(2, 10), day=2)], "VR-01") == ()

    def test_an_item_after_the_last_day_is_an_error(self) -> None:
        assert only([item(starts=at(9, 9), ends=at(9, 10), day=9)], "VR-01") == ("VR-01",)

    def test_the_window_is_extended_by_the_arrival_flight(self) -> None:
        """§10.6: "extended by the arrival and departure flight times". A
        flight landing before the first day begins moves the boundary back —
        the transfer it needs is a legitimate part of the trip."""
        early = at(1, 0) - timedelta(hours=3)
        flying = trip_facts(arrival=FlightFacts(scheduled_at=early, gateway_location="airport"))
        arrival_item = item(starts=early, ends=early + timedelta(hours=1))
        assert only([arrival_item], "VR-01", trip=flying) == ()

    def test_without_the_flight_the_same_item_is_outside(self) -> None:
        """The paired case, so the previous test is about the extension rather
        than about the window being wide anyway."""
        early = at(1, 0) - timedelta(hours=15)
        assert only([item(starts=early, ends=early + timedelta(hours=1))], "VR-01") == ("VR-01",)


class TestVR02Overlaps:
    def test_back_to_back_items_do_not_overlap(self) -> None:
        """Touching is not overlapping: one ends exactly as the next begins."""
        items = [
            item(1, starts=at(1, 9), ends=at(1, 10)),
            item(2, starts=at(1, 10), ends=at(1, 11)),
        ]
        assert only(items, "VR-02") == ()

    def test_overlapping_items_are_an_error(self) -> None:
        items = [
            item(1, starts=at(1, 9), ends=at(1, 11)),
            item(2, starts=at(1, 10), ends=at(1, 12)),
        ]
        assert only(items, "VR-02") == ("VR-02",)

    def test_a_stay_may_overlap_everything(self) -> None:
        """ "No two **non-STAY** items". A stay anchor spans the night and
        would otherwise overlap every item in the day by construction."""
        items = [
            item(1, Kind.STAY_CHECK_IN, starts=at(1, 0), ends=at(1, 23)),
            item(2, starts=at(1, 10), ends=at(1, 12)),
        ]
        assert only(items, "VR-02") == ()

    def test_items_on_different_days_do_not_overlap(self) -> None:
        items = [
            item(1, day=1, starts=at(1, 9), ends=at(1, 11)),
            item(2, day=2, starts=at(1, 10), ends=at(1, 12)),
        ]
        assert only(items, "VR-02") == ()


class TestVR03Reachable:
    """Built the way §10.4 builds a day, so the rule is exercised on the shape
    the planner actually emits: `A -> transfer -> B`, with the leg ending one
    buffer before B begins."""

    def _day(
        self, *, gap_minutes: int, travel_minutes: int, buffer_minutes: int = 15
    ) -> list[ItemFacts]:
        """`buffer_minutes` defaults to the Appendix B activity buffer, because
        that is what §10.4 line 14 leaves in front of an activity. A fixture
        that left none would be testing a day the sequencer never builds."""
        first_end = at(1, 10)
        second_start = first_end + timedelta(minutes=gap_minutes)
        leg_end = second_start - timedelta(minutes=buffer_minutes)
        return [
            item(1, starts=at(1, 9), ends=first_end, start_location="a", end_location="a"),
            item(
                3,
                Kind.TRANSFER,
                starts=leg_end - timedelta(minutes=travel_minutes),
                ends=leg_end,
                start_location="a",
                end_location="b",
                travel_seconds=travel_minutes * 60,
            ),
            item(
                2,
                starts=second_start,
                ends=second_start + timedelta(hours=1),
                start_location="b",
                end_location="b",
            ),
        ]

    def test_a_sufficient_gap_passes(self) -> None:
        assert only(self._day(gap_minutes=90, travel_minutes=45), "VR-03") == ()

    def test_an_insufficient_gap_is_an_error(self) -> None:
        """Forty-five minutes of driving into a thirty-minute gap. The leg has
        to set off before the earlier item finishes, which is what the rule
        sees."""
        assert only(self._day(gap_minutes=30, travel_minutes=45), "VR-03") == ("VR-03",)

    def test_the_buffer_counts_against_the_gap(self) -> None:
        """ "gap >= travel time **+ buffer**". Forty-five minutes of driving
        fits a fifty-minute gap on its own, and does not once the fifteen
        minutes before an activity are added."""
        assert only(self._day(gap_minutes=50, travel_minutes=45), "VR-03") == ("VR-03",)

    def test_own_transport_skips_the_rule(self) -> None:
        """§10.9: "Trip entirely without transfers (tourist self-drives) —
        supported; VR-03 is skipped between items marked own_transport"."""
        day = self._day(gap_minutes=5, travel_minutes=120)
        day = [replace(i, own_transport=True) for i in day]
        assert only(day, "VR-03") == ()

    def test_an_unmeasured_gap_between_different_places_is_caught(self) -> None:
        """No transfer at all, and no own_transport flag. The travel time is
        zero because nothing measured it, so the arithmetic alone would pass a
        five-minute gap between two different places — the buffer is what
        catches it."""
        day = [
            item(1, starts=at(1, 9), ends=at(1, 10), start_location="a", end_location="a"),
            item(
                2,
                starts=at(1, 10, 5),
                ends=at(1, 11),
                start_location="b",
                end_location="b",
            ),
        ]
        assert only(day, "VR-03", buffers=Buffers(activity_minutes=15)) == ("VR-03",)

    def test_a_transfer_may_not_set_off_before_the_item_it_follows_ends(self) -> None:
        """The pair the earlier draft of this rule skipped entirely. A leg
        that departs at 09:30 from an activity running until 10:00 is not a
        tight itinerary, it is an impossible one."""
        day = [
            item(1, starts=at(1, 9), ends=at(1, 10), start_location="a", end_location="a"),
            item(
                3,
                Kind.TRANSFER,
                starts=at(1, 9, 30),
                ends=at(1, 10, 30),
                start_location="a",
                end_location="b",
                travel_seconds=3600,
            ),
        ]
        assert only(day, "VR-03") == ("VR-03",)


class TestVR04NoNightCoveredTwice:
    """The v1.2 amendment. "Own arrangement" is the normal case."""

    def test_one_stay_per_night_passes(self) -> None:
        nights = trip_facts().nights
        stay = item(1, Kind.STAY_CHECK_IN, covered_nights=nights)
        assert only([stay], "VR-04") == ()

    def test_two_stays_claiming_one_night_is_an_error(self) -> None:
        """A real contradiction: two anchors say the tourist sleeps in two
        places, and the planner would route transfers to both."""
        night = (START,)
        items = [
            item(1, Kind.STAY_CHECK_IN, covered_nights=night),
            item(2, Kind.STAY_CHECK_IN, covered_nights=night),
        ]
        assert only(items, "VR-04") == ("VR-04",)

    def test_one_stay_arriving_as_two_anchors_is_still_one_stay(self) -> None:
        """The shape the application layer actually produces, and the one this
        class did not test until a real trip failed on it.

        `sequencing.Rank` says it outright: *"A STAY appears twice because a
        stay spans nights: on the day it begins it is a check-in and sorts
        late, and on the day it ends it is a check-out and sorts early. The
        application layer expands one `itinerary_item` row into whichever of
        the two anchors fall on the day being sequenced."*

        So one row reaches this rule as two `ItemFacts` **sharing an
        `item_id`**, each carrying the same `covered_nights`. Counting
        occurrences rather than distinct stays made every night of every trip
        with accommodation a blocking error — which is a trip that cannot be
        priced at all.

        Both anchors carry the whole tuple rather than a half each: the nights
        a stay covers are a property of the stay, and the anchors are two views
        of it. A rule that assumed otherwise would be relying on the sequencer
        to split them.
        """
        nights = trip_facts().nights
        items = [
            item(4, Kind.STAY_CHECK_IN, covered_nights=nights),
            item(4, Kind.STAY_CHECK_OUT, covered_nights=nights),
        ]
        assert only(items, "VR-04") == ()

    def test_two_stays_are_still_caught_when_each_has_two_anchors(self) -> None:
        """The other half. De-duplicating by `item_id` must not blind the rule
        to the contradiction it exists for."""
        night = (START,)
        items = [
            item(1, Kind.STAY_CHECK_IN, covered_nights=night),
            item(1, Kind.STAY_CHECK_OUT, covered_nights=night),
            item(2, Kind.STAY_CHECK_IN, covered_nights=night),
            item(2, Kind.STAY_CHECK_OUT, covered_nights=night),
        ]
        assert only(items, "VR-04") == ("VR-04",)

    def test_the_finding_names_each_stay_once(self) -> None:
        """`item_ids` is what the client renders an inline fix against (§10.6).
        Naming one stay twice would offer the tourist two rows to remove and
        only one to find."""
        night = (START,)
        findings = validate(
            [
                item(1, Kind.STAY_CHECK_IN, covered_nights=night),
                item(1, Kind.STAY_CHECK_OUT, covered_nights=night),
                item(2, Kind.STAY_CHECK_IN, covered_nights=night),
                item(2, Kind.STAY_CHECK_OUT, covered_nights=night),
            ],
            trip=trip_facts(),
            buffers=Buffers(),
            limits=Limits(),
        )
        vr04 = [f for f in findings if f.code == "VR-04"]
        assert len(vr04) == 1
        assert vr04[0].item_ids == (1, 2)

    def test_an_uncovered_night_is_not_a_vr04_error(self) -> None:
        """The amendment's whole point, and what makes §10.9's "day trip with
        no accommodation: supported" true. It is VR-16's warning instead."""
        codes = run([item(1)])
        assert "VR-04" not in codes
        assert "VR-16" in codes

    def test_consecutive_stays_may_meet_without_overlapping(self) -> None:
        """§10.9: "Accommodation split across two properties — supported:
        multiple STAY items with contiguous, non-overlapping date ranges"."""
        nights = trip_facts().nights
        items = [
            item(1, Kind.STAY_CHECK_IN, covered_nights=nights[:2]),
            item(2, Kind.STAY_CHECK_IN, covered_nights=nights[2:]),
        ]
        assert only(items, "VR-04") == ()


class TestVR05PartySize:
    def test_a_party_within_the_range_passes(self) -> None:
        assert only([item(min_pax=2, max_pax=12)], "VR-05") == ()

    def test_too_few_people_is_an_error(self) -> None:
        assert only([item(min_pax=4, max_pax=12)], "VR-05") == ("VR-05",)

    def test_too_many_people_is_an_error(self) -> None:
        big = trip_facts(party=PartyFacts(adults=14))
        assert only([item(min_pax=2, max_pax=12)], "VR-05", trip=big) == ("VR-05",)

    def test_infants_do_not_count_towards_capacity(self) -> None:
        """§16.3's capacity is seats, and a lap infant does not occupy one.
        Stated here because it is an assumption rather than a quotation."""
        party = trip_facts(party=PartyFacts(adults=2, infants=2))
        assert only([item(min_pax=2, max_pax=2)], "VR-05", trip=party) == ()

    def test_a_stay_asserts_no_occupancy(self) -> None:
        """The v1.2 amendment: the room-occupancy half is deferred to v2 with
        room_type. A stay anchor books no room, so there is nothing to check
        the party against."""
        huge = trip_facts(party=PartyFacts(adults=20))
        stay = item(1, Kind.STAY_CHECK_IN, covered_nights=trip_facts().nights)
        assert only([stay], "VR-05", trip=huge) == ()


class TestVR06BookingCutoff:
    def test_booking_before_the_cutoff_passes(self) -> None:
        departs = at(3, 9)
        planned = departs - timedelta(hours=48)
        assert (
            only(
                [item(starts=planned, ends=planned, departs_at=departs, booking_cutoff_hours=24)],
                "VR-06",
            )
            == ()
        )

    def test_booking_inside_the_cutoff_is_an_error(self) -> None:
        """§16.6: the cutoff exists because a provider cannot staff a
        last-minute booking."""
        departs = at(3, 9)
        planned = departs - timedelta(hours=2)
        assert only(
            [item(starts=planned, ends=planned, departs_at=departs, booking_cutoff_hours=24)],
            "VR-06",
        ) == ("VR-06",)

    def test_an_activity_with_no_bound_departure_is_not_checked(self) -> None:
        """There is nothing to be late for yet, and `models.py` deliberately
        permits that draft state."""
        assert only([item(booking_cutoff_hours=24)], "VR-06") == ()


class TestVR07ArrivalTransfer:
    def _trip(self) -> TripFacts:
        return trip_facts(arrival=FlightFacts(scheduled_at=at(1, 8), gateway_location="airport"))

    def test_a_pickup_after_the_processing_buffer_passes(self) -> None:
        pickup = item(1, Kind.TRANSFER, starts=at(1, 9), ends=at(1, 10), is_airport_arrival=True)
        assert only([pickup], "VR-07", trip=self._trip()) == ()

    def test_a_pickup_before_it_is_an_error(self) -> None:
        """The 45 minutes are immigration, baggage and customs. §11.1 calls
        this "the single highest-anxiety moment of the journey"."""
        pickup = item(1, Kind.TRANSFER, starts=at(1, 8, 20), ends=at(1, 9), is_airport_arrival=True)
        assert only([pickup], "VR-07", trip=self._trip()) == ("VR-07",)

    def test_an_actual_arrival_overrides_the_schedule(self) -> None:
        """§11.2: the tourist or driver may update the actual arrival, and the
        system re-times the transfer. A flight that landed two hours late
        makes a previously valid pickup invalid."""
        delayed = trip_facts(
            arrival=FlightFacts(
                scheduled_at=at(1, 8), actual_at=at(1, 10), gateway_location="airport"
            )
        )
        pickup = item(1, Kind.TRANSFER, starts=at(1, 9), ends=at(1, 10), is_airport_arrival=True)
        assert only([pickup], "VR-07", trip=delayed) == ("VR-07",)


class TestVR08DepartureTransfer:
    def _trip(self) -> TripFacts:
        return trip_facts(departure=FlightFacts(scheduled_at=at(6, 18), gateway_location="airport"))

    def test_arriving_three_hours_early_passes(self) -> None:
        leg = item(
            1, Kind.TRANSFER, day=6, starts=at(6, 14), ends=at(6, 15), is_airport_departure=True
        )
        assert only([leg], "VR-08", trip=self._trip()) == ()

    def test_arriving_too_late_is_an_error(self) -> None:
        leg = item(
            1, Kind.TRANSFER, day=6, starts=at(6, 16), ends=at(6, 17), is_airport_departure=True
        )
        assert only([leg], "VR-08", trip=self._trip()) == ("VR-08",)

    def test_it_uses_the_scheduled_time_not_an_actual_one(self) -> None:
        """A departure has no actual time to know: the flight has not left.
        VR-08 works from the schedule, which is the only fact available."""
        leg = item(
            1, Kind.TRANSFER, day=6, starts=at(6, 14), ends=at(6, 15), is_airport_departure=True
        )
        assert only([leg], "VR-08", trip=self._trip()) == ()


class TestVR09ActiveListings:
    def test_an_active_listing_passes(self) -> None:
        assert only([item(listing_is_active=True)], "VR-09") == ()

    def test_a_withdrawn_listing_is_an_error(self) -> None:
        assert only([item(listing_is_active=False)], "VR-09") == ("VR-09",)

    def test_an_inactive_provider_is_an_error(self) -> None:
        assert only([item(provider_is_active=False)], "VR-09") == ("VR-09",)

    def test_an_unknown_provider_does_not_pass_as_an_active_one(self) -> None:
        """The half of this rule that cannot be answered yet.

        `provider` is a Phase 1 skeleton, so `provider_is_active` is None for
        every item today. None means *unknown*, and the test that matters is
        that it is not quietly the same as True: the rule branches on `is
        False`, so the day a provider check arrives it changes behaviour
        rather than being absorbed.
        """
        unknown = item(provider_is_active=None)
        assert unknown.provider_is_active is not True
        assert only([unknown], "VR-09") == ()


class TestVR10OneCurrency:
    def test_matching_currencies_pass(self) -> None:
        assert only([item(currency="NZD")], "VR-10") == ()

    def test_an_item_with_no_price_is_not_a_mismatch(self) -> None:
        """A stay anchor and free time carry no currency at all (ADR 0013)."""
        assert only([item(currency=None)], "VR-10") == ()

    def test_a_foreign_currency_is_an_error(self) -> None:
        """§18.5 permits no mixed-currency total, and this module has no
        business knowing an exchange rate."""
        assert only([item(currency="USD")], "VR-10") == ("VR-10",)

    def test_all_offenders_are_named_in_one_finding(self) -> None:
        """One banner, not one per item: the tourist's fix is the same for all
        of them, and §24.14 renders the list."""
        items = [item(1, currency="USD"), item(2, currency="EUR")]
        findings = validate(items, trip=trip_facts(), buffers=Buffers(), limits=Limits())
        vr10 = [f for f in findings if f.code == "VR-10"]
        assert len(vr10) == 1
        assert vr10[0].item_ids == (1, 2)


class TestVR12OpeningHours:
    def test_an_open_attraction_passes(self) -> None:
        assert only([item(kind=Kind.ATTRACTION, is_open_at_scheduled_time=True)], "VR-12") == ()

    def test_a_closed_attraction_warns(self) -> None:
        assert only([item(kind=Kind.ATTRACTION, is_open_at_scheduled_time=False)], "VR-12") == (
            "VR-12",
        )

    def test_unpublished_hours_do_not_warn(self) -> None:
        """§15.2: null hours mean "not published", which is not the same as
        closed. Warning here would put a caution on most of the catalogue and
        teach tourists to ignore the banner."""
        assert only([item(kind=Kind.ATTRACTION, is_open_at_scheduled_time=None)], "VR-12") == ()

    def test_it_is_a_warning_not_an_error(self) -> None:
        findings = validate(
            [item(kind=Kind.ATTRACTION, is_open_at_scheduled_time=False)],
            trip=trip_facts(),
            buffers=Buffers(),
            limits=Limits(),
        )
        assert next(f for f in findings if f.code == "VR-12").severity is Severity.WARNING


class TestVR13ItemsPerDay:
    def test_five_items_is_within_the_limit(self) -> None:
        items = [item(n, starts=at(1, 8 + n), ends=at(1, 8 + n)) for n in range(1, 6)]
        assert only(items, "VR-13") == ()

    def test_six_items_warns(self) -> None:
        items = [item(n, starts=at(1, 8 + n), ends=at(1, 8 + n)) for n in range(1, 7)]
        assert only(items, "VR-13") == ("VR-13",)

    def test_transfers_do_not_count(self) -> None:
        """They are the planner's own work. Counting them would warn a tourist
        about a day the planner made busy rather than one they did."""
        items = [item(n, starts=at(1, 8 + n), ends=at(1, 8 + n)) for n in range(1, 6)]
        items += [
            item(20 + n, Kind.TRANSFER, starts=at(1, 8 + n), ends=at(1, 8 + n)) for n in range(1, 5)
        ]
        assert only(items, "VR-13") == ()

    def test_the_limit_is_configurable(self) -> None:
        """NFR-M07. An administrator lowering it during a busy season must not
        need a deployment."""
        items = [item(n, starts=at(1, 8 + n), ends=at(1, 8 + n)) for n in range(1, 4)]
        assert only(items, "VR-13", limits=Limits(items_per_day=2)) == ("VR-13",)


class TestVR14TravelMinutesPerDay:
    def _legs(self, minutes: int) -> list[ItemFacts]:
        return [
            item(
                1,
                Kind.TRANSFER,
                starts=at(1, 9),
                ends=at(1, 10),
                travel_seconds=minutes * 60,
            )
        ]

    def test_four_hours_is_within_the_limit(self) -> None:
        assert only(self._legs(240), "VR-14") == ()

    def test_more_than_four_hours_warns(self) -> None:
        assert only(self._legs(300), "VR-14") == ("VR-14",)

    def test_travel_is_summed_across_the_day(self) -> None:
        legs = [
            item(1, Kind.TRANSFER, starts=at(1, 9), ends=at(1, 11), travel_seconds=7200),
            item(2, Kind.TRANSFER, starts=at(1, 14), ends=at(1, 17), travel_seconds=7300),
        ]
        assert only(legs, "VR-14") == ("VR-14",)


class TestVR15AgeRequirement:
    def test_an_adults_only_party_is_not_warned(self) -> None:
        assert only([item(min_age=8)], "VR-15") == ()

    def test_an_activity_with_no_age_limit_is_not_warned(self) -> None:
        family = trip_facts(party=PartyFacts(adults=2, children=2))
        assert only([item(min_age=None)], "VR-15", trip=family) == ()

    def test_children_plus_an_age_limit_warns(self) -> None:
        """Deliberately conservative: §7.5.10 stores counts, not ages, so the
        platform cannot tell whether a particular child qualifies. Asking the
        tourist to check is the honest reading of what is knowable —
        inventing an age to compare against would not be."""
        family = trip_facts(party=PartyFacts(adults=2, children=1))
        assert only([item(min_age=8)], "VR-15", trip=family) == ("VR-15",)

    def test_an_infant_counts_as_a_child_for_this_warning(self) -> None:
        """It does not count towards capacity (VR-05) and does count here.
        The two rules ask different questions: one is about seats, the other
        about whether a person young enough to be excluded is present."""
        with_infant = trip_facts(party=PartyFacts(adults=2, infants=1))
        assert only([item(min_age=8)], "VR-15", trip=with_infant) == ("VR-15",)


class TestVR16NightsWithoutAStay:
    def test_a_fully_covered_trip_is_silent(self) -> None:
        stay = item(1, Kind.STAY_CHECK_IN, covered_nights=trip_facts().nights)
        assert only([stay], "VR-16") == ()

    def test_an_uncovered_night_warns(self) -> None:
        nights = trip_facts().nights
        stay = item(1, Kind.STAY_CHECK_IN, covered_nights=nights[:-1])
        assert only([stay], "VR-16") == ("VR-16",)

    def test_it_warns_once_however_many_nights(self) -> None:
        assert only([item(1)], "VR-16") == ("VR-16",)

    def test_it_names_no_item(self) -> None:
        """The rule is about items that are absent, so there is nothing to
        anchor it to. §24.14 renders it as a trip-level banner."""
        findings = validate([item(1)], trip=trip_facts(), buffers=Buffers(), limits=Limits())
        assert next(f for f in findings if f.code == "VR-16").item_ids == ()

    def test_a_day_trip_has_no_nights_at_all(self) -> None:
        """§10.9: "Day trip with no accommodation — supported". A trip that
        starts and ends on the same date has zero nights, so there is nothing
        to be uncovered."""
        day_trip = trip_facts(start_date=START, end_date=START)
        assert day_trip.nights == ()
        assert only([item(1)], "VR-16", trip=day_trip) == ()


class TestVR17ActivityAfterLanding:
    def _trip(self) -> TripFacts:
        return trip_facts(arrival=FlightFacts(scheduled_at=at(1, 8), gateway_location="airport"))

    def test_an_activity_four_hours_after_landing_passes(self) -> None:
        assert only([item(starts=at(1, 12), ends=at(1, 14))], "VR-17", trip=self._trip()) == ()

    def test_an_activity_within_three_hours_warns(self) -> None:
        """Not an error — a tourist who wants to go straight from the airport
        to a sunset cruise may. It warns because a delayed flight turns it
        into a missed activity nobody will refund."""
        assert only([item(starts=at(1, 10), ends=at(1, 12))], "VR-17", trip=self._trip()) == (
            "VR-17",
        )

    def test_an_activity_before_landing_is_vr01_not_vr17(self) -> None:
        """Different failures need different messages: one is unreachable
        because the tourist is in the air, the other merely tight."""
        codes = run([item(starts=at(1, 6), ends=at(1, 7))], trip=self._trip())
        assert "VR-17" not in codes

    def test_no_flight_means_no_warning(self) -> None:
        assert only([item(starts=at(1, 10), ends=at(1, 12))], "VR-17") == ()


class TestTripWindow:
    def test_it_runs_from_local_midnight_to_local_midnight(self) -> None:
        """The trip's dates are local dates. Auckland is UTC+12 in June, so a
        trip starting on 1 June begins at 12:00 UTC on 31 May — computing the
        window in UTC would silently drop the first half of the first day."""
        start, end = trip_facts().window()
        assert start.isoformat() == "2027-06-01T00:00:00+12:00"
        assert end.date() == END

    @pytest.mark.parametrize("days", [0, 1, 5])
    def test_a_trip_has_one_fewer_night_than_it_has_dates(self, days: int) -> None:
        """A trip from the 10th to the 15th has five nights, not six: the last
        date is a departure day."""
        facts = trip_facts(end_date=START + timedelta(days=days))
        assert len(facts.nights) == days
