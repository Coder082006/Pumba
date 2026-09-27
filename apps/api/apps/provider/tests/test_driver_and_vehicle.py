"""The two tables a dispatcher reads before it offers anybody work — §7.5.4,
§7.5.5, ADR 0029.

What is asserted here is only what the database itself promises, because the
eligibility filter of §11.6 is tested against its own pure function and a
constraint tested through a service is a constraint nobody has tested. The four
that matter are the ones a bug would quietly get past: a plate reused while the
first vehicle is still alive, a driver account re-registered after closure, a
vehicle class the transport module has never heard of, and a minibus with no
seats in it.

Nothing here constructs a `destination` or a `user`. Both belong to modules
`provider` may not see, so the columns are bare integers by design (ADR 0012).
"""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.gis.geos import Polygon
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.provider.models import (
    Driver,
    Provider,
    ProviderTypeChoice,
    Vehicle,
    VehicleClassCode,
    VerifyStatus,
)

pytestmark = pytest.mark.django_db

NEXT_YEAR = dt.date(2027, 12, 31)

#: Every helper-made driver is a different person unless a test says otherwise.
#: Sharing one id would make `driver_user_unique_alive` fire inside tests that
#: are about something else entirely — and, worse, would let a test wrapped in
#: `pytest.raises(IntegrityError)` pass on the wrong constraint.
_people = itertools.count(501)


def an_operator(**overrides: Any) -> Provider:
    values: dict[str, Any] = {
        "legal_name": "Zanzibar Coast Transfers Ltd",
        "trading_name": "Coast Transfers",
        "provider_type": ProviderTypeChoice.TRANSPORT,
        "contact_email": "dispatch@example.com",
        "contact_phone": "+255700000010",
        "region_id": 1,
        "verify_status": VerifyStatus.VERIFIED,
        "verified_at": timezone.now(),
    }
    values.update(overrides)
    return Provider.objects.create(**values)


def a_driver(provider: Provider | None = None, **overrides: Any) -> Driver:
    values: dict[str, Any] = {
        "user_id": next(_people),
        "provider": provider or an_operator(),
        "licence_number": b"ciphertext",
        "licence_expires_on": NEXT_YEAR,
        "home_destination_id": 3,
    }
    values.update(overrides)
    return Driver.objects.create(**values)


def a_vehicle(driver: Driver | None = None, **overrides: Any) -> Vehicle:
    owner = driver or a_driver()
    values: dict[str, Any] = {
        "driver": owner,
        "provider": owner.provider,
        "plate_number": "T 123 ABC",
        "make": "Toyota",
        "model": "Noah",
        "year": 2019,
        "colour": "white",
        "seat_capacity": 6,
        "luggage_capacity": 4,
        "vehicle_class": VehicleClassCode.VAN,
        "insurance_expires_on": NEXT_YEAR,
        "inspection_expires_on": NEXT_YEAR,
    }
    values.update(overrides)
    return Vehicle.objects.create(**values)


class TestDriver:
    def test_a_new_driver_starts_unverified_and_offline(self) -> None:
        """§7.5.4's defaults, and they are the safe ones: a driver nobody has
        checked is not in any candidate list."""
        driver = a_driver()

        assert driver.verify_status == VerifyStatus.DRAFT
        assert driver.is_online is False
        assert driver.completed_trips == 0

    def test_a_new_driver_is_credited_with_a_perfect_acceptance_rate(self) -> None:
        """§11.6 scores on acceptance rate. Starting at zero would score a
        driver on offers nobody has made them yet, and they would never rank
        high enough to receive one — supply that can never develop."""
        assert a_driver().acceptance_rate == Decimal("100.00")

    def test_languages_default_to_english_and_are_not_shared(self) -> None:
        """§7.5.4's default of English. The second half matters because a
        mutable default shared between rows is the oldest bug in Python."""
        first = a_driver()
        second = a_driver(user_id=502)
        first.languages.append("sw")

        assert second.languages == ["en"]

    def test_one_person_may_hold_one_driver_record(self) -> None:
        a_driver(user_id=600)

        with pytest.raises(IntegrityError), transaction.atomic():
            a_driver(user_id=600)

    def test_a_closed_account_does_not_reserve_the_person_forever(self) -> None:
        """Partial on `deleted_at`, as `user_phone_unique_alive` is: somebody
        who left and came back is not locked out by their own history."""
        first = a_driver(user_id=601)
        first.deleted_at = timezone.now()
        first.save(update_fields=["deleted_at"])

        assert a_driver(user_id=601).pk != first.pk

    def test_a_service_area_is_optional_and_holds_a_polygon(self) -> None:
        """§11.6 rule 6 excludes a driver whose area is set and excludes the
        pickup. An unset area excludes nobody."""
        assert a_driver().service_area is None

        bounded = a_driver(
            user_id=602,
            service_area=Polygon(
                ((39.1, -6.2), (39.4, -6.2), (39.4, -5.9), (39.1, -5.9), (39.1, -6.2))
            ),
        )
        bounded.refresh_from_db()
        assert bounded.service_area is not None

    def test_the_licence_number_is_stored_as_bytes(self) -> None:
        """§30.4: envelope-encrypted, so the column holds a
        `ports.crypto.Ciphertext` blob and never a readable licence."""
        driver = a_driver(licence_number=b"\x00\x01not-text")
        driver.refresh_from_db()

        assert bytes(driver.licence_number) == b"\x00\x01not-text"

    def test_a_negative_trip_count_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_driver(user_id=603, completed_trips=-1)


class TestVehicle:
    def test_a_vehicle_belongs_to_a_driver_and_to_an_operator(self) -> None:
        """§7.5.5 carries both. A driver who changes operator does not take the
        fleet's minibus with them."""
        vehicle = a_vehicle()

        assert vehicle.provider_id == vehicle.driver.provider_id

    def test_two_live_vehicles_may_not_share_a_plate(self) -> None:
        """A plate is how the tourist identifies the car at arrivals (§11.9).
        Two of them is the failure that protocol exists to prevent."""
        a_vehicle()

        with pytest.raises(IntegrityError), transaction.atomic():
            a_vehicle(plate_number="T 123 ABC")

    def test_a_retired_vehicle_releases_its_plate(self) -> None:
        retired = a_vehicle()
        retired.deleted_at = timezone.now()
        retired.save(update_fields=["deleted_at"])

        assert a_vehicle(plate_number="T 123 ABC").pk != retired.pk

    def test_a_class_transport_has_never_heard_of_is_refused(self) -> None:
        """The code is a string because `provider` may not import `transport`
        (ADR 0012). This constraint is what stops the two vocabularies
        drifting apart in silence."""
        with pytest.raises(IntegrityError), transaction.atomic():
            a_vehicle(vehicle_class="LIMOUSINE")

    def test_a_vehicle_with_no_seats_is_refused(self) -> None:
        """§11.6 rule 3 matches seats against pax. A zero would match nobody
        and, worse, would look like a data-entry slip rather than a refusal."""
        with pytest.raises(IntegrityError), transaction.atomic():
            a_vehicle(seat_capacity=0)

    def test_luggage_capacity_may_be_zero_but_not_negative(self) -> None:
        """A tuk-tuk with no boot is a real vehicle; minus one bag is not."""
        assert a_vehicle(luggage_capacity=0).luggage_capacity == 0

        with pytest.raises(IntegrityError), transaction.atomic():
            a_vehicle(plate_number="T 999 ZZZ", luggage_capacity=-1)

    def test_an_implausible_year_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_vehicle(year=1899)

    def test_it_reads_as_the_tourist_will_see_it(self) -> None:
        """§11.4 discloses colour, make, model and plate together, so the
        string form is the disclosure in miniature."""
        assert str(a_vehicle()) == "white Toyota Noah (T 123 ABC)"
