"""The `provider` table — SRS §7.5.3.

Each constraint is exercised against the database, positive and negative,
because a CHECK that is declared and never violated in a test is a CHECK nobody
knows is actually there.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.provider.models import Provider, VerifyStatus

pytestmark = pytest.mark.django_db


def make(**overrides: Any) -> Provider:
    fields: dict[str, Any] = {
        "legal_name": "Safari Blue Tours Limited",
        "trading_name": "Safari Blue",
        "provider_type": "ACTIVITY",
        "contact_email": "bookings@safariblue.example",
        "contact_phone": "+255700000001",
        "region_id": 1,
    }
    fields.update(overrides)
    return Provider.objects.create(**fields)


def refused(**overrides: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make(**overrides)


class TestTheDefaults:
    def test_a_new_provider_is_a_draft(self) -> None:
        """§7.5.3: `verify_status` defaults to 'DRAFT'. Nobody is sellable on
        arrival."""
        assert make().verify_status == VerifyStatus.DRAFT

    def test_payout_currency_defaults_to_tzs(self) -> None:
        assert make().payout_currency == "TZS"

    def test_ratings_start_empty(self) -> None:
        provider = make()
        assert (str(provider.rating_avg), provider.rating_count) == ("0.00", 0)

    def test_it_gets_a_uuid_for_the_outside_world(self) -> None:
        """Hard rule 8: the integer id never leaves the database."""
        assert make().public_id is not None


class TestTheConstraints:
    def test_an_unknown_type_is_refused(self) -> None:
        refused(provider_type="RESTAURANT")

    def test_an_unknown_status_is_refused(self) -> None:
        refused(verify_status="APPROVED")

    def test_a_verified_provider_without_a_time_is_refused(self) -> None:
        refused(verify_status=VerifyStatus.VERIFIED, verified_at=None)

    def test_a_verified_provider_with_a_time_is_accepted(self) -> None:
        make(verify_status=VerifyStatus.VERIFIED, verified_at=timezone.now())

    def test_a_suspended_provider_keeps_its_verification_time(self) -> None:
        """One-way on purpose: a suspension withdraws a verification that did
        happen, and the timestamp is when."""
        make(verify_status=VerifyStatus.SUSPENDED, verified_at=timezone.now())

    def test_a_negative_rating_count_is_refused(self) -> None:
        refused(rating_count=-1)


class TestTheEmailIsCaseInsensitive:
    def test_it_matches_regardless_of_case(self) -> None:
        """`CITEXT`, as §7.5.3 declares — two spellings of one mailbox are one
        mailbox."""
        make(contact_email="Bookings@SafariBlue.example")
        assert Provider.objects.filter(contact_email="bookings@safariblue.example").exists()


class TestSoftDeletion:
    def test_a_deleted_provider_is_hidden_but_kept(self) -> None:
        """R23 makes a booking's provider RESTRICT; the row outlives listing."""
        provider = make()
        provider.delete()
        assert not Provider.objects.filter(pk=provider.pk).exists()
        assert Provider.all_objects.filter(pk=provider.pk).exists()


class TestWhoSellsATransfer:
    """ADR 0025's addendum: the verified TRANSPORT provider in the origin region."""

    def test_the_verified_transport_provider_in_the_region(self) -> None:
        from apps.provider.services import transport_provider_for

        make(provider_type="ACTIVITY", verify_status="VERIFIED", verified_at=timezone.now())
        seller = make(
            legal_name="North Cars Limited",
            provider_type="TRANSPORT",
            verify_status="VERIFIED",
            verified_at=timezone.now(),
        )
        found = transport_provider_for(1)
        assert found is not None and found.id == seller.id

    def test_an_unverified_or_suspended_one_is_not_chosen(self) -> None:
        """BR-037, before anything is booked."""
        from apps.provider.services import transport_provider_for

        make(provider_type="TRANSPORT")
        make(
            legal_name="Suspended Cars Limited",
            provider_type="TRANSPORT",
            verify_status="SUSPENDED",
            verified_at=timezone.now(),
        )
        assert transport_provider_for(1) is None

    def test_another_region_s_provider_is_not_chosen(self) -> None:
        from apps.provider.services import transport_provider_for

        make(provider_type="TRANSPORT", verify_status="VERIFIED", verified_at=timezone.now())
        assert transport_provider_for(2) is None

    def test_the_oldest_wins_so_the_answer_is_stable(self) -> None:
        from apps.provider.services import transport_provider_for

        first = make(
            legal_name="First Cars Limited",
            provider_type="TRANSPORT",
            verify_status="VERIFIED",
            verified_at=timezone.now(),
        )
        make(
            legal_name="Second Cars Limited",
            provider_type="TRANSPORT",
            verify_status="VERIFIED",
            verified_at=timezone.now(),
        )
        found = transport_provider_for(1)
        assert found is not None and found.id == first.id
