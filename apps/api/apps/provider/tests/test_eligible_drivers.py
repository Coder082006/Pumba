"""§11.6's hard filter — who is allowed to be offered a transfer at all.

This is the half of dispatch that says *no*. The scoring function decides an
order; this decides membership, and getting it wrong is the more expensive
mistake in both directions: a driver wrongly excluded never earns, and a driver
wrongly included turns up at an airport uninsured.

Every rule §11.6 lists is asserted here except rule 5. That one asks about
`driver_assignment`, which belongs to `transport` and which this module may not
see (§6.4), so the dispatcher applies it — and ADR 0029's EXCLUDE constraint
catches whatever slips between its check and the write.

The fixtures are deliberately one driver with one vehicle, changed one field at
a time, because a filter test that builds five different drivers proves only
that the query returned something.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.contrib.gis.geos import Polygon
from django.utils import timezone

from apps.provider import services
from apps.provider.models import (
    Driver,
    Provider,
    ProviderTypeChoice,
    Vehicle,
    VehicleClassCode,
    VerifyStatus,
)

pytestmark = pytest.mark.django_db

#: Stone Town, near enough. The pickup every test in this file asks about.
PICKUP_LAT = -6.163
PICKUP_LNG = 39.191

NOW = dt.datetime(2027, 3, 1, 8, 0, tzinfo=dt.UTC)
#: Comfortably beyond `dispatch.offline_offer_hours`, so rule 7 lets an offline
#: driver through unless a test moves the pickup closer.
PICKUP_AT = NOW + dt.timedelta(days=3)
NEXT_YEAR = dt.date(2028, 1, 1)

_emails = itertools.count(1)

#: §7.5.1's `user.status`, as strings. `apps.identity.models` is private to
#: `identity` (§6.5 rule 1), and a test is not exempt from that — the registry
#: lookup below is how the rest of the suite reaches another module's table
#: when it genuinely has to.
ACTIVE = "ACTIVE"
SUSPENDED = "SUSPENDED"


def _a_user(*, status: str) -> Any:
    model = django_apps.get_model("identity", "User")
    return model.objects.create(
        email=f"driver{next(_emails)}@example.com", password="x", status=status
    )


def an_operator() -> Provider:
    return Provider.objects.create(
        legal_name="Zanzibar Coast Transfers Ltd",
        trading_name="Coast Transfers",
        provider_type=ProviderTypeChoice.TRANSPORT,
        contact_email=f"fleet{next(_emails)}@example.com",
        contact_phone="+255700000011",
        region_id=1,
        verify_status=VerifyStatus.VERIFIED,
        verified_at=timezone.now(),
    )


def a_working_driver(**overrides: Any) -> Vehicle:
    """One verified driver, one verified van, everything in date.

    Returned as the vehicle because that is what a candidate *is* (§11.6 rules
    3 and 4 are about the car), and the driver hangs off it.
    """
    driver_fields = {
        "verify_status": VerifyStatus.VERIFIED,
        "licence_expires_on": NEXT_YEAR,
        "is_online": True,
        "home_destination_id": 3,
        "service_area": None,
    }
    vehicle_fields: dict[str, Any] = {
        "plate_number": f"T {next(_emails):03d} ABC",
        "make": "Toyota",
        "model": "Noah",
        "year": 2019,
        "colour": "white",
        "seat_capacity": 6,
        "luggage_capacity": 4,
        "vehicle_class": VehicleClassCode.VAN,
        "insurance_expires_on": NEXT_YEAR,
        "inspection_expires_on": NEXT_YEAR,
        "verify_status": VerifyStatus.VERIFIED,
        "is_active": True,
    }
    account_status = overrides.pop("user_status", ACTIVE)
    for key, value in overrides.items():
        if key in driver_fields:
            driver_fields[key] = value
        else:
            vehicle_fields[key] = value

    user = _a_user(status=account_status)
    provider = an_operator()
    driver = Driver.objects.create(
        user_id=int(user.pk),
        provider=provider,
        licence_number=b"ciphertext",
        **driver_fields,
    )
    return Vehicle.objects.create(driver=driver, provider=provider, **vehicle_fields)


def candidates(**overrides: Any) -> tuple[Any, ...]:
    ask: dict[str, Any] = {
        "pickup_at": PICKUP_AT,
        "pickup_lat": PICKUP_LAT,
        "pickup_lng": PICKUP_LNG,
        "pax": 4,
        "luggage": 3,
        "vehicle_class": VehicleClassCode.VAN,
        "now": NOW,
    }
    ask.update(overrides)
    return services.eligible_drivers(**ask)


class TestWhoGetsThrough:
    def test_a_driver_in_good_standing_is_a_candidate(self) -> None:
        vehicle = a_working_driver()

        found = candidates()

        assert [row.vehicle_id for row in found] == [vehicle.pk]
        assert found[0].driver_id == vehicle.driver_id

    def test_a_candidate_is_a_driver_and_a_vehicle_together(self) -> None:
        """§11.6 rules 3 and 4 ask about the car, so a driver with two cars is
        two candidates — and may qualify on one and not the other."""
        vehicle = a_working_driver()
        Vehicle.objects.create(
            driver=vehicle.driver,
            provider=vehicle.provider,
            plate_number="T 777 SAL",
            make="Toyota",
            model="Corolla",
            year=2020,
            colour="silver",
            seat_capacity=3,
            luggage_capacity=2,
            vehicle_class=VehicleClassCode.VAN,
            insurance_expires_on=NEXT_YEAR,
            inspection_expires_on=NEXT_YEAR,
            verify_status=VerifyStatus.VERIFIED,
        )

        # The saloon seats three and the party is four.
        assert [row.vehicle_id for row in candidates()] == [vehicle.pk]
        assert len(candidates(pax=2, luggage=1)) == 2


class TestTheDriverThemselves:
    def test_an_unverified_driver_is_excluded(self) -> None:
        """§11.6 rule 1."""
        a_working_driver(verify_status=VerifyStatus.SUBMITTED)

        assert candidates() == ()

    def test_a_suspended_account_is_excluded(self) -> None:
        """§11.6 rule 1's second half, which lives in `identity` — a driver
        record can be spotless while the person behind it is suspended."""
        a_working_driver(user_status=SUSPENDED)

        assert candidates() == ()

    def test_a_licence_that_expires_before_the_pickup_is_excluded(self) -> None:
        """§11.6 rule 2, and the point of "before the pickup" rather than
        "today": a licence valid now and expired on the day of the transfer is
        a licence that is not valid for the transfer."""
        a_working_driver(licence_expires_on=dt.date(2027, 3, 2))

        assert candidates() == ()
        assert len(candidates(pickup_at=NOW + dt.timedelta(hours=13))) == 1

    def test_an_offline_driver_is_offered_work_that_is_far_enough_away(self) -> None:
        """§11.6 rule 7: scheduled work goes to offline drivers too, because
        nobody sits in the app three days before an airport run."""
        a_working_driver(is_online=False)

        assert len(candidates()) == 1

    def test_an_offline_driver_is_not_offered_work_starting_soon(self) -> None:
        a_working_driver(is_online=False)

        assert candidates(pickup_at=NOW + dt.timedelta(hours=2)) == ()


class TestTheVehicle:
    def test_a_car_with_too_few_seats_is_excluded(self) -> None:
        """§11.6 rule 3, and seat capacity excludes the driver (§7.5.5), so
        four passengers need four seats and not five."""
        a_working_driver(seat_capacity=3)

        assert candidates() == ()
        assert len(candidates(pax=3)) == 1

    def test_a_car_with_too_little_luggage_space_is_excluded(self) -> None:
        a_working_driver(luggage_capacity=2)

        assert candidates() == ()

    def test_the_wrong_class_is_excluded(self) -> None:
        """§11.6 rule 4. A tourist who paid for a van is not sent a saloon,
        even a roomy one."""
        a_working_driver(vehicle_class=VehicleClassCode.STANDARD, seat_capacity=9)

        assert candidates() == ()

    def test_expired_insurance_blocks_dispatch(self) -> None:
        """TC-091, and the reason this filter lives in `provider` rather than
        in the dispatcher: it is true of everyone who asks."""
        a_working_driver(insurance_expires_on=dt.date(2027, 2, 1))

        assert candidates() == ()

    def test_an_expired_inspection_blocks_dispatch(self) -> None:
        a_working_driver(inspection_expires_on=dt.date(2027, 2, 1))

        assert candidates() == ()

    def test_an_unverified_or_retired_vehicle_is_excluded(self) -> None:
        a_working_driver(verify_status=VerifyStatus.VERIFIED, is_active=False)

        assert candidates() == ()


class TestTheServiceArea:
    def test_a_driver_with_no_service_area_may_go_anywhere(self) -> None:
        """§7.5.4 calls it an "optional operating boundary", so its absence is
        permission and not a gap in the data."""
        a_working_driver(service_area=None)

        assert len(candidates()) == 1

    def test_a_pickup_inside_the_area_is_allowed(self) -> None:
        a_working_driver(
            service_area=Polygon(
                ((39.1, -6.3), (39.3, -6.3), (39.3, -6.0), (39.1, -6.0), (39.1, -6.3))
            )
        )

        assert len(candidates()) == 1

    def test_a_pickup_outside_the_area_is_excluded(self) -> None:
        """§11.6 rule 6. Pemba is an hour's flight from Stone Town."""
        a_working_driver(
            service_area=Polygon(
                ((39.6, -5.4), (39.9, -5.4), (39.9, -5.0), (39.6, -5.0), (39.6, -5.4))
            )
        )

        assert candidates() == ()


class TestWhatComesBack:
    def test_every_figure_the_score_needs_travels_with_the_candidate(self) -> None:
        """§11.6's score reads four driver figures. A dispatcher that had to go
        back for any of them would be reading `provider`'s tables directly."""
        a_working_driver()

        candidate = candidates()[0]

        assert candidate.rating_avg is not None
        assert candidate.acceptance_rate is not None
        assert candidate.completed_trips == 0
        assert candidate.home_destination_id == 3
        assert candidate.languages == ("en",)

    def test_nothing_returned_is_an_orm_row(self) -> None:
        """§6.5 rule 5. Asserted here and not only in the architecture test,
        because this is the boundary a dispatcher crosses on every offer."""
        a_working_driver()

        assert not hasattr(candidates()[0], "_meta")

    def test_asking_when_nobody_is_available_is_not_an_error(self) -> None:
        """TC-090 turns on an empty list, so it has to be an ordinary answer
        rather than something the dispatcher has to catch."""
        assert candidates() == ()
