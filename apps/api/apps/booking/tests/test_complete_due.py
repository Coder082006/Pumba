"""Starting and finishing a booking on the clock — §20.2, BR-071, ADR 0028.

The transitions have been declared since Phase 7 and nothing could fire them:
`service_start_reached` and `service_end_reached` are facts, and nobody was
supplying them. That matters more than it sounds, because **completion is what
earns an operator their money** — BR-071 accrues there — so an unfinished
booking is an unpaid provider.

What is asserted is the sweeper's judgement, not its plumbing: it advances what
is due, leaves alone what is not, and never resurrects something a tourist
cancelled.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.booking import services
from apps.booking.models import Booking, BookingStatusHistory
from apps.booking.tests import scenario
from apps.common.events import subscribe

pytestmark = pytest.mark.django_db


def a_confirmed_booking(**times: dt.datetime) -> Booking:
    """One confirmed booking, with its times set where the test needs them."""
    built = scenario.build()
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    row = Booking.objects.get()
    for field, value in times.items():
        setattr(row, field, value)
    if times:
        row.save(update_fields=list(times))
    return row


class TestWhatIsDue:
    def test_a_service_whose_start_has_passed_is_under_way(self) -> None:
        row = a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=1),
            ends_at=timezone.now() + dt.timedelta(hours=1),
        )

        assert services.complete_due() == {"started": 1, "completed": 0}

        row.refresh_from_db()
        assert row.status == "IN_PROGRESS"

    def test_a_service_whose_end_has_passed_completes(self) -> None:
        """Both edges in one run: a booking that finished while nobody was
        looking should not need two sweeps to catch up."""
        row = a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=4),
            ends_at=timezone.now() - dt.timedelta(hours=1),
        )

        counts = services.complete_due()

        row.refresh_from_db()
        assert counts == {"started": 1, "completed": 1}
        assert row.status == "COMPLETED"
        assert row.completed_at is not None

    def test_a_service_that_has_not_started_is_left_alone(self) -> None:
        row = a_confirmed_booking(
            starts_at=timezone.now() + dt.timedelta(days=2),
            ends_at=timezone.now() + dt.timedelta(days=2, hours=3),
        )

        assert services.complete_due() == {"started": 0, "completed": 0}

        row.refresh_from_db()
        assert row.status == "CONFIRMED"

    def test_running_twice_changes_nothing_the_second_time(self) -> None:
        """§8.8: every job idempotent. A second run finds the work done."""
        a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=4),
            ends_at=timezone.now() - dt.timedelta(hours=1),
        )
        services.complete_due()

        assert services.complete_due() == {"started": 0, "completed": 0}


class TestWhatItMustNotTouch:
    def test_a_cancelled_booking_is_not_resurrected(self) -> None:
        """The machine refuses it, rather than a condition here remembering to.

        A tourist who cancelled an afternoon dive at noon must not find it
        marked as having happened at four — and the reason this is safe is
        §20.2, not an `if` somebody could forget to write.
        """
        row = a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=4),
            ends_at=timezone.now() - dt.timedelta(hours=1),
        )
        services.cancel_booking(row.public_id, actor=services.Actor.TOURIST, actor_user_id=None)

        assert services.complete_due() == {"started": 0, "completed": 0}

        row.refresh_from_db()
        assert row.status == "CANCELLED"


class TestWhatItLeavesBehind:
    def test_each_move_is_recorded_with_its_reason(self) -> None:
        """BR-032: history names an actor and a reason for every change — and
        "the clock" is a reason a support agent can act on, where an empty
        field is a mystery."""
        a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=4),
            ends_at=timezone.now() - dt.timedelta(hours=1),
        )

        services.complete_due()

        rows = list(BookingStatusHistory.objects.order_by("id"))
        assert [row.to_status for row in rows] == [
            "PENDING",
            "CONFIRMED",
            "IN_PROGRESS",
            "COMPLETED",
        ]
        assert all(row.actor_role for row in rows[-2:])
        assert "time passed" in rows[-1].reason

    def test_completion_announces_the_figures_frozen_at_confirmation(
        self, django_capture_on_commit_callbacks
    ) -> None:  # type: ignore[no-untyped-def]
        """TC-110's other half. The event carries what was agreed at
        confirmation, so a ledger accrues that and never what a rule says
        today — and it carries strings, because a Decimal that crossed as a
        float would lose a cent on the way to an account.

        The callbacks are run explicitly because `publish` defers to commit and
        a test transaction never commits; that deferral is §8.9's guarantee, so
        the test bends to it.
        """
        seen: list[services.BookingCompleted] = []
        subscribe(services.BookingCompleted, seen.append)
        row = a_confirmed_booking(
            starts_at=timezone.now() - dt.timedelta(hours=4),
            ends_at=timezone.now() - dt.timedelta(hours=1),
        )

        with django_capture_on_commit_callbacks(execute=True):
            services.complete_due()

        assert len(seen) == 1
        event = seen[0]
        row.refresh_from_db()
        assert event.reference == row.reference
        assert event.provider_id == row.provider_id
        assert event.gross_amount == str(row.gross_amount)
        assert event.commission_amount == str(row.commission_amount)
        assert event.net_amount == str(row.net_amount)
        # The split the ledger will post: what the operator is owed and what
        # the platform earned, adding up to what the tourist paid.
        assert row.gross_amount == row.commission_amount + row.net_amount
