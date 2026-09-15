"""§37.7 and §41.8's acceptance criteria, as tests.

    §37.7: "TC-060, TC-061, TC-100 to TC-103 pass; every transition in Section
    20.2 has a passing positive and negative test."

    §41.8: "Every transition in Section 20.2 succeeds when legal and is rejected
    with 409 when not. Status history captures actor and reason for every
    change. Basket creation snapshots the cancellation policy and commission
    rate. Vouchers generate for every confirmed booking."

Two halves. `TestTheWholeJourney` walks it once over HTTP, from a priced trip to
a refunded cancellation, as a tourist and an administrator would. `TestEach
CriterionIsStillAsserted` names the tests that carry each criterion and fails if
one disappears — the way `test_acceptance_phase6.py` guards the §12.4 worked
example — so a criterion cannot quietly stop being proved.
"""

from __future__ import annotations

import importlib
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps

from apps.booking.models import Booking, BookingStatusHistory, BookingVoucher
from apps.booking.tests import scenario
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _data(response: Any) -> Any:
    assert response.status_code in (200, 201), getattr(response, "data", response.content)
    return response.data["data"]


class TestTheWholeJourney:
    """Price, reserve, confirm, download, preview, cancel — over the wire."""

    def test_a_trip_goes_from_a_price_to_a_refunded_cancellation(self) -> None:
        tourist = signed_in_as()
        profile = (
            django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
        )
        assert profile is not None
        built = scenario.build(tourist_id=int(profile.id), adults=2, days_ahead=30)
        trip = built.trip_public_id

        quote = _data(
            tourist.post(f"/api/v1/trips/{trip}/quote", HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex)
        )

        # TC-060: the basket.
        basket = _data(
            tourist.post(
                f"/api/v1/trips/{trip}/confirm",
                {"quote_token": quote["quote_token"]},
                format="json",
                HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
            )
        )
        assert basket["trip_status"] == "PENDING_PAYMENT"
        booking = Booking.objects.get()
        assert booking.status == "PENDING"
        assert booking.cancellation_policy_snapshot["code"] == "MODERATE_7D"
        assert booking.commission_rate == Decimal("15.00")

        # Payment is Phase 8; BR-038's control stands in for the capture.
        admin = signed_in_as(Role.SUPER_ADMIN)
        _data(
            admin.post(
                f"/api/v1/admin/bookings/{booking.public_id}/force-transition",
                {"status": "CONFIRMED", "reason": "Acceptance walk: capture stand-in."},
                format="json",
            )
        )

        # §41.8: a voucher for every confirmed booking.
        pdf = tourist.post(f"/api/v1/bookings/{booking.public_id}/voucher")
        assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"

        # BR-043: the preview is the refund.
        preview = _data(tourist.get(f"/api/v1/bookings/{booking.public_id}/cancellation-preview"))
        cancelled = _data(
            tourist.post(f"/api/v1/bookings/{booking.public_id}/cancel", {}, format="json")
        )
        assert preview["refund_amount"] == cancelled["refund_amount"]
        assert cancelled["booking"]["status"] == "CANCELLED"

        # §41.8: history names actor and reason for every change.
        history = list(BookingStatusHistory.objects.order_by("id"))
        assert [h.to_status for h in history] == ["PENDING", "CONFIRMED", "CANCELLED"]
        assert all(h.actor_role and h.reason for h in history)
        assert BookingVoucher.objects.count() == 1


#: Criterion -> (module, class, test) that proves it.
CRITERIA: dict[str, list[tuple[str, str, str]]] = {
    "TC-060 Confirm creates the basket": [
        (
            "apps.booking.tests.test_basket",
            "TestTC060ConfirmCreatesTheBasket",
            "test_the_bookings_are_pending",
        ),
        (
            "apps.booking.tests.test_basket",
            "TestTC060ConfirmCreatesTheBasket",
            "test_the_policy_is_snapshotted",
        ),
        (
            "apps.booking.tests.test_basket",
            "TestTC060ConfirmCreatesTheBasket",
            "test_the_commission_rate_is_snapshotted_and_the_amount_is_not",
        ),
        (
            "apps.booking.tests.test_basket",
            "TestTC060ConfirmCreatesTheBasket",
            "test_the_trip_is_pending_payment",
        ),
    ],
    "TC-061 Confirm with an expired quote": [
        (
            "apps.booking.tests.test_basket",
            "TestTC061AnExpiredQuote",
            "test_it_is_refused_and_nothing_is_created",
        ),
    ],
    "TC-100 Refund at the tier boundary": [
        (
            "apps.booking.tests.test_domain_cancellation",
            "TestTC100AndTC101TheBoundary",
            "test_tc_100_seven_days_and_a_second_is_a_full_refund",
        ),
    ],
    "TC-101 Just inside the tier": [
        (
            "apps.booking.tests.test_domain_cancellation",
            "TestTC100AndTC101TheBoundary",
            "test_tc_101_a_second_inside_seven_days_is_half",
        ),
    ],
    "TC-102 Provider cancellation": [
        (
            "apps.booking.tests.test_domain_cancellation",
            "TestTC102SupplyFailure",
            "test_everything_comes_back_inside_every_tier",
        ),
        (
            "apps.booking.tests.test_cancel",
            "TestTC102AProviderCancels",
            "test_the_tourist_gets_everything_back_whatever_the_tier",
        ),
    ],
    "TC-103 Refund exceeds capture": [
        (
            "apps.booking.tests.test_domain_cancellation",
            "TestTC103BR044",
            "test_tc_103_refunding_200_of_a_110_booking_is_refused",
        ),
    ],
    "§20.2 every transition, positive": [
        (
            "apps.booking.tests.test_domain_lifecycle",
            "TestEveryTransitionSucceedsWhenLegal",
            "test_positive",
        ),
    ],
    "§20.2 every transition, negative": [
        (
            "apps.booking.tests.test_domain_lifecycle",
            "TestEveryTransitionIsRefusedWhenItsGuardIsNot",
            "test_negative",
        ),
        (
            "apps.booking.tests.test_domain_lifecycle",
            "TestUndeclaredEdgesAreRefused",
            "test_every_undeclared_pair_is_illegal_for_everyone",
        ),
    ],
    "§41.8 vouchers for every confirmed booking": [
        (
            "apps.booking.tests.test_voucher",
            "TestEveryConfirmedBookingHasOne",
            "test_confirmation_issues_the_first_voucher",
        ),
    ],
    "§33.10 on-request timeout": [
        (
            "apps.booking.tests.test_on_request",
            "TestTheTimeout",
            "test_an_unanswered_booking_is_cancelled_with_a_full_refund",
        ),
    ],
    "§33.10 trip-level cancellation with mixed policies": [
        (
            "apps.booking.tests.test_cancel_trip",
            "TestMixedPolicies",
            "test_each_component_is_refunded_under_its_own_policy",
        ),
    ],
    "§33.10 hold expiry mid-payment": [
        (
            "apps.booking.tests.test_confirm",
            "TestStep9TheHardCase",
            "test_a_dead_hold_whose_seats_the_sweeper_gave_away_fails_that_booking",
        ),
    ],
    "§33.10 cancellation at each tier boundary, one second either side": [
        (
            "apps.booking.tests.test_domain_cancellation",
            "TestEveryTierOneSecondEitherSide",
            "test_the_percent_changes_at_exactly_the_threshold",
        ),
    ],
}


class TestEachCriterionIsStillAsserted:
    @pytest.mark.parametrize(
        ("criterion", "module", "cls", "test"),
        [(criterion, *entry) for criterion, entries in CRITERIA.items() for entry in entries],
        ids=lambda value: str(value)[:48],
    )
    def test_the_test_that_proves_it_exists(
        self, criterion: str, module: str, cls: str, test: str
    ) -> None:
        holder = getattr(importlib.import_module(module), cls, None)
        assert holder is not None, f"{criterion}: {module}.{cls} is gone"
        assert callable(getattr(holder, test, None)), f"{criterion}: {cls}.{test} is gone"
