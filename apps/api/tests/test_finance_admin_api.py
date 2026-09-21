"""The finance console over HTTP — §22.2, §22.5, §26.7, §27.10, BR-075.

Under `tests/` because an administrator spans `identity` and four other
modules, and §6.4 lets none of their suites import the others.

The assertions worth making here are the ones about *who*: a commission rate is
a commercial term and a payout is money leaving the platform, and §5.2 gives
those to different people on purpose. A console that let the wrong one through
would be a control that exists only in a document.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.common.authz import Role
from apps.finance import services as finance
from apps.finance.models import CommissionRule, Payout, PayoutStatus, ProviderBalance
from apps.provider.models import Provider
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

RULES = reverse("v1:administration:admin-commission-rule-list")
PAYOUTS = reverse("v1:administration:admin-payout-list")
REPORT = reverse("v1:administration:admin-finance-report")


def a_provider() -> Provider:
    return Provider.objects.create(
        legal_name="Matemwe Blue Water Divers Ltd",
        trading_name="Matemwe Blue Water Divers",
        provider_type="ACTIVITY",
        contact_email="dive@example.com",
        contact_phone="+255700000001",
        region_id=1,
        verify_status="VERIFIED",
        verified_at=timezone.now(),
    )


def a_payout(provider_id: int, amount: str = "200.00") -> Payout:
    """A batch, as the row behind it.

    The service hands back a frozen DTO (§6.5 rule 5), and these tests assert
    what was *stored* — so the row is read back deliberately rather than by
    keeping a live model object around.
    """
    ProviderBalance.objects.create(
        provider_id=provider_id, currency="USD", available_amount=Decimal(amount)
    )
    built = finance.build_payout_batch(
        provider_id=provider_id,
        currency="USD",
        period_start=timezone.localdate() - dt.timedelta(days=7),
        period_end=timezone.localdate(),
    )
    assert built is not None
    return Payout.objects.get(public_id=built.public_id)


class TestCommissionRules:
    def test_a_catalogue_administrator_adds_one(self) -> None:
        """§27.11 puts commercial rules with the consoles that set prices."""
        admin = signed_in_as(Role.CATALOGUE_ADMIN)
        seller = a_provider()

        response = admin.post(
            RULES,
            {
                "scope": "PROVIDER",
                "provider": str(seller.public_id),
                "method": "PERCENT",
                "percent": "18.00",
                "priority": 10,
            },
            format="json",
        )

        assert response.status_code == 201, response.data
        rule = CommissionRule.objects.get()
        assert rule.provider_id == seller.pk
        assert rule.percent == Decimal("18.00")

    def test_a_rule_with_a_scope_and_no_target_is_refused(self) -> None:
        """A PROVIDER rule naming nobody matches nothing — and reads exactly
        like a rule that matches everybody."""
        admin = signed_in_as(Role.CATALOGUE_ADMIN)

        response = admin.post(
            RULES, {"scope": "PROVIDER", "method": "PERCENT", "percent": "18.00"}, format="json"
        )

        assert response.status_code == 422
        assert not CommissionRule.objects.exists()

    def test_a_percentage_rule_without_a_percentage_is_refused(self) -> None:
        admin = signed_in_as(Role.CATALOGUE_ADMIN)

        response = admin.post(RULES, {"scope": "GLOBAL", "method": "PERCENT"}, format="json")

        assert response.status_code == 422

    def test_changing_a_rate_is_audited_with_what_it_was(self) -> None:
        """A rate that moved from 15 to 20 is a different story from one that
        was always 20 — and only the first explains an operator's invoice."""
        admin = signed_in_as(Role.CATALOGUE_ADMIN)
        created = admin.post(
            RULES, {"scope": "GLOBAL", "method": "PERCENT", "percent": "15.00"}, format="json"
        ).data["data"]

        admin.patch(
            reverse(
                "v1:administration:admin-commission-rule-detail",
                kwargs={"public_id": created["id"]},
            ),
            {"percent": "20.00"},
            format="json",
        )

        from django.apps import apps as django_apps

        entries = django_apps.get_model("administration", "AuditLog").objects.filter(
            action="finance.commission_rule_changed"
        )
        assert entries.count() == 2
        changed = entries.order_by("-id").first()
        assert changed is not None
        assert changed.before["percent"] == "15.00"

    def test_a_tourist_cannot_see_the_rules(self) -> None:
        assert signed_in_as().get(RULES).status_code == 403


class TestPayouts:
    def test_finance_approves_and_then_releases(self) -> None:
        """BR-075: approval comes first, and only finance gives it."""
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)
        payout = a_payout(a_provider().pk)

        approved = finance_officer.post(
            reverse(
                "v1:administration:admin-payout-approve", kwargs={"public_id": payout.public_id}
            )
        )
        released = finance_officer.post(
            reverse(
                "v1:administration:admin-payout-release", kwargs={"public_id": payout.public_id}
            ),
            {"rail_reference": "BANK-2027-0007"},
            format="json",
        )

        assert approved.status_code == 200, approved.data
        assert released.status_code == 200, released.data
        payout.refresh_from_db()
        assert payout.status == PayoutStatus.PAID
        assert payout.rail_reference == "BANK-2027-0007"
        assert payout.approved_by_user_id is not None

    def test_a_payout_cannot_be_released_before_it_is_approved(self) -> None:
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)
        payout = a_payout(a_provider().pk)

        response = finance_officer.post(
            reverse(
                "v1:administration:admin-payout-release", kwargs={"public_id": payout.public_id}
            ),
            {"rail_reference": "BANK-2027-0008"},
            format="json",
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "PAYOUT_NOT_RELEASABLE"

    def test_a_release_without_a_reference_is_refused(self) -> None:
        """No rail moves the money (ADR 0028), so the reference is the only
        evidence a transfer happened at all.

        Refused at the door as a malformed request rather than deeper as a
        conflict — the service checks it too, for callers that are not this
        endpoint.
        """
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)
        payout = a_payout(a_provider().pk)
        finance.approve_payout(payout.public_id, approved_by_user_id=1)

        response = finance_officer.post(
            reverse(
                "v1:administration:admin-payout-release", kwargs={"public_id": payout.public_id}
            ),
            {"rail_reference": "  "},
            format="json",
        )

        assert response.status_code == 422
        payout.refresh_from_db()
        assert payout.status == PayoutStatus.APPROVED

    def test_a_catalogue_administrator_may_not_approve_money(self) -> None:
        """§5.2 separates them deliberately: the person who sets a rate is not
        the person who signs off paying it."""
        catalogue_admin = signed_in_as(Role.CATALOGUE_ADMIN)
        payout = a_payout(a_provider().pk)

        response = catalogue_admin.post(
            reverse(
                "v1:administration:admin-payout-approve", kwargs={"public_id": payout.public_id}
            )
        )

        assert response.status_code == 403
        payout.refresh_from_db()
        assert payout.status == PayoutStatus.DRAFT

    def test_the_list_shows_the_bookings_behind_a_batch(self) -> None:
        """§22.5's line items: an operator who disagrees with a figure can be
        shown which services it is made of."""
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)
        a_payout(a_provider().pk)

        response = finance_officer.get(PAYOUTS)

        assert response.status_code == 200
        assert "items" in response.data["data"][0]


class TestEarningsAndTheReport:
    def test_an_administrator_can_read_what_a_provider_is_owed(self) -> None:
        """§26.7 until Phase 11's portal: somebody at Pumba answers the
        question the operator cannot yet ask themselves."""
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)
        seller = a_provider()
        ProviderBalance.objects.create(
            provider_id=seller.pk, currency="USD", pending_amount=Decimal("93.50")
        )

        response = finance_officer.get(
            reverse(
                "v1:administration:admin-provider-earnings",
                kwargs={"public_id": seller.public_id},
            )
        )

        assert response.status_code == 200
        assert response.data["data"]["balances"][0]["pending"] == "93.50"

    def test_the_report_comes_from_the_ledger(self) -> None:
        """§22.7: "generated from the ledger, never from the booking table"."""
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)

        response = finance_officer.get(REPORT)

        assert response.status_code == 200
        assert set(response.data["data"]["accounts"]) == {
            "PLATFORM_CLEARING",
            "PROVIDER_PAYABLE",
            "PLATFORM_REVENUE",
            "PLATFORM_EXPENSE",
            "PLATFORM_FX",
        }
        assert response.data["data"]["exceptions"] == []

    def test_a_provider_that_does_not_exist_is_a_404(self) -> None:
        finance_officer = signed_in_as(Role.FINANCE_OFFICER)

        response = finance_officer.get(
            reverse("v1:administration:admin-provider-earnings", kwargs={"public_id": uuid.uuid4()})
        )

        assert response.status_code == 404


def _data(response: Any) -> Any:
    return response.data["data"]
