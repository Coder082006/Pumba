"""The booking tables — SRS §7.5.12, R24, R26, ADR 0025.

Every CHECK is violated at least once against the database, because a
constraint nobody has seen refuse a row is a constraint nobody knows is there.
The append-only trigger gets the same treatment: an UPDATE must fail, not merely
be absent from the code.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.gis.geos import Point
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from apps.booking.models import (
    Booking,
    BookingActivity,
    BookingStatus,
    BookingStatusHistory,
    BookingTransfer,
)
from apps.common.reference import REFERENCE_RE, new_reference

pytestmark = pytest.mark.django_db

START = timezone.now() + timedelta(days=10)


def booking(**overrides: Any) -> Booking:
    fields: dict[str, Any] = {
        "reference": new_reference("BKG"),
        "trip_id": 1,
        "tourist_id": 1,
        "provider_id": 1,
        "booking_type": "ACTIVITY",
        "starts_at": START,
        "ends_at": START + timedelta(hours=4),
        "pax_count": 2,
        "gross_amount": Decimal("180000.00"),
        "fee_amount": Decimal("9000.00"),
        "currency": "TZS",
        "commission_rate": Decimal("15.00"),
        "cancellation_policy_id": 1,
        "cancellation_policy_snapshot": {"code": "MODERATE_7D", "tiers": []},
    }
    fields.update(overrides)
    return Booking.objects.create(**fields)


def refused(**overrides: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        booking(**overrides)


class TestTheDefaults:
    def test_a_new_booking_is_pending(self) -> None:
        """§7.5.12's default. DRAFT exists in the machine but no v1 path writes
        it: a basket line is created PENDING by `POST /trips/{id}/confirm`."""
        assert booking().status == BookingStatus.PENDING

    def test_the_reference_has_the_specified_shape(self) -> None:
        match = REFERENCE_RE.match(booking().reference)
        assert match is not None and match["prefix"] == "BKG"

    def test_the_version_starts_at_zero(self) -> None:
        assert booking().version == 0


class TestTheTypeConstraint:
    @pytest.mark.parametrize("kind", ["ACTIVITY", "TRANSFER"])
    def test_the_v1_types_are_accepted(self, kind: str) -> None:
        booking(booking_type=kind, cancellation_policy_id=1)

    def test_accommodation_is_reserved_not_live(self) -> None:
        """ADR 0013: in the enum, refused by the CHECK."""
        refused(booking_type="ACCOMMODATION")

    def test_an_activity_must_name_the_policy_it_was_sold_under(self) -> None:
        refused(booking_type="ACTIVITY", cancellation_policy_id=None)

    def test_a_transfer_need_not(self) -> None:
        booking(booking_type="TRANSFER", cancellation_policy_id=None)


class TestTheMoneyConstraints:
    def test_a_negative_amount_is_refused(self) -> None:
        refused(gross_amount=Decimal("-1.00"))

    def test_a_rate_above_a_hundred_percent_is_refused(self) -> None:
        refused(commission_rate=Decimal("100.01"))

    def test_a_commission_amount_without_its_net_is_refused(self) -> None:
        """§20.8 step 14 computes them together; half a settlement is refused."""
        refused(commission_amount=Decimal("27000.00"), net_amount=None)

    def test_a_settled_commission_is_accepted(self) -> None:
        booking(commission_amount=Decimal("27000.00"), net_amount=Decimal("153000.00"))

    def test_a_settlement_without_a_frozen_rate_is_refused(self) -> None:
        refused(
            commission_rate=None,
            commission_amount=Decimal("27000.00"),
            net_amount=Decimal("153000.00"),
        )


class TestTheShapeConstraints:
    def test_a_booking_that_ends_before_it_starts_is_refused(self) -> None:
        refused(ends_at=START - timedelta(minutes=1))

    def test_an_empty_party_is_refused(self) -> None:
        refused(pax_count=0)

    def test_an_unknown_status_is_refused(self) -> None:
        refused(status="ON_HOLD")

    def test_a_cancellation_names_when_who_and_why_together(self) -> None:
        refused(cancelled_at=timezone.now())

    def test_a_whole_cancellation_is_accepted(self) -> None:
        booking(
            status=BookingStatus.CANCELLED,
            cancelled_at=timezone.now(),
            cancelled_by="TOURIST",
            cancellation_reason="TOURIST_REQUEST",
        )

    def test_awaiting_a_provider_requires_a_deadline(self) -> None:
        """ADR 0025 decision 7: the deadline is the rule, so it must exist."""
        refused(status=BookingStatus.AWAITING_PROVIDER, response_due_at=None)


class TestTheTransferSubtype:
    def transfer(self, **overrides: Any) -> BookingTransfer:
        fields: dict[str, Any] = {
            "booking": booking(booking_type="TRANSFER", cancellation_policy_id=None),
            "pickup_point": Point(39.19, -6.16, srid=4326),
            "dropoff_point": Point(39.29, -5.73, srid=4326),
            "pickup_at": START,
            "estimate_quality": "APPROXIMATE",
            "vehicle_class": "STANDARD",
            "corridor_id": 7,
        }
        fields.update(overrides)
        return BookingTransfer.objects.create(**fields)

    def test_a_corridor_priced_leg_is_accepted(self) -> None:
        self.transfer()

    def test_a_tariff_priced_leg_is_accepted(self) -> None:
        self.transfer(corridor_id=None, tariff_id=3)

    def test_naming_both_rules_is_refused(self) -> None:
        """ADR 0023: one leg, one rule. Two would make the price unreproducible."""
        with pytest.raises(IntegrityError), transaction.atomic():
            self.transfer(corridor_id=7, tariff_id=3)

    def test_naming_neither_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            self.transfer(corridor_id=None, tariff_id=None)

    def test_an_unknown_estimate_quality_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            self.transfer(estimate_quality="GUESSED")


class TestTheActivitySubtype:
    def test_it_is_one_to_one_with_its_booking(self) -> None:
        """R24: exactly one subtype per booking."""
        parent = booking()
        BookingActivity.objects.create(
            booking=parent, activity_id=1, activity_departure_id=1, pax_adult=2, meeting_at=START
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            BookingActivity.objects.create(
                booking=parent,
                activity_id=1,
                activity_departure_id=1,
                pax_adult=2,
                meeting_at=START,
            )


class TestTheHistoryIsAppendOnly:
    def history(self, parent: Booking | None = None) -> BookingStatusHistory:
        return BookingStatusHistory.objects.create(
            booking=parent or booking(),
            from_status=None,
            to_status="PENDING",
            actor_role="TOURIST",
            occurred_at=timezone.now(),
        )

    def test_a_row_can_be_written(self) -> None:
        self.history()

    def test_a_row_cannot_be_changed(self) -> None:
        row = self.history()
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            BookingStatusHistory.objects.filter(pk=row.pk).update(reason="rewritten")

    def test_a_row_cannot_be_deleted(self) -> None:
        row = self.history()
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            BookingStatusHistory.objects.filter(pk=row.pk).delete()

    def test_a_booking_with_history_cannot_be_deleted(self) -> None:
        """§7.2: booking records are "never soft-deleted or hard-deleted". The
        ORM's cascade reaches the history first, and the trigger refuses it, so
        the rule holds for a careless `.delete()` as well as a deliberate one."""
        parent = booking()
        self.history(parent)
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            parent.delete()
        assert Booking.objects.filter(pk=parent.pk).exists()

    def test_it_must_name_an_actor(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            BookingStatusHistory.objects.create(
                booking=booking(), to_status="PENDING", actor_role="", occurred_at=timezone.now()
            )
