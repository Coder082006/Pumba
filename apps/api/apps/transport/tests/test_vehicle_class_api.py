"""`GET /transport/vehicle-classes` — SRS §9.3.4, §12.4, §24.16.

§9.3.4's auth column for this route is `—`, so it answers without a principal.
§24.16 is why that matters: the airport-pickup screen shows vehicle-class cards
with capacity, and a tourist comparing them has not necessarily signed in.

Two things are asserted that a happy-path test would not reach. That the
payload publishes **no price**, because a class has none — a route priced for a
class does — and that it publishes **no internal id**, because §7.2 says
sequential integers never leave the database and a serializer is the last place
that rule is enforced before the wire.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.transport.models import VehicleClass

pytestmark = pytest.mark.django_db

URL = reverse("v1:transport:vehicle-class-list")


def _seed() -> dict[str, VehicleClass]:
    """§12.4's four classes, in Appendix C's order."""
    spec = [
        ("STANDARD", "Standard", 4, 3, False, 1),
        ("COMFORT", "Comfort", 4, 3, True, 2),
        ("VAN", "Van", 7, 6, True, 3),
        ("MINIBUS", "Minibus", 14, 12, True, 4),
    ]
    return {
        code: VehicleClass.objects.create(
            code=code,
            name=name,
            seats=seats,
            luggage_capacity=bags,
            has_air_conditioning=ac,
            display_order=order,
        )
        for code, name, seats, bags, ac, order in spec
    }


def _get() -> object:
    return APIClient().get(URL)


class TestItAnswersWithoutAPrincipal:
    def test_an_anonymous_client_gets_the_list(self) -> None:
        _seed()
        response = _get()
        assert response.status_code == 200  # type: ignore[attr-defined]
        assert len(response.data["data"]) == 4  # type: ignore[attr-defined]

    def test_the_four_classes_come_back_in_display_order(self) -> None:
        """§24.16 renders these as a row of cards, and a row whose order
        depended on insertion would reshuffle when an administrator edited
        one."""
        _seed()
        codes = [row["code"] for row in _get().data["data"]]  # type: ignore[attr-defined]
        assert codes == ["STANDARD", "COMFORT", "VAN", "MINIBUS"]

    def test_capacity_is_published(self) -> None:
        """The numbers §12.4 filters on: "only classes whose seats >= pax and
        luggage >= luggage_count"."""
        _seed()
        van = next(r for r in _get().data["data"] if r["code"] == "VAN")  # type: ignore[attr-defined]
        assert van["seats"] == 7
        assert van["luggage_capacity"] == 6

    def test_air_conditioning_distinguishes_comfort_from_standard(self) -> None:
        """§12.4 gives both four seats and three bags. Without this field the
        two cards are identical and the upsell is unexplainable."""
        _seed()
        rows = {r["code"]: r for r in _get().data["data"]}  # type: ignore[attr-defined]
        assert rows["STANDARD"]["has_air_conditioning"] is False
        assert rows["COMFORT"]["has_air_conditioning"] is True


class TestWhatTheWireDoesNotCarry:
    def test_no_price_is_published(self) -> None:
        """§9.3.4 calls it "capacity and indicative pricing". A class has no
        price of its own; a route priced for a class does. A number here would
        be a quote nobody asked for, that goes stale without anything
        happening, on an endpoint built to be cached.
        """
        _seed()
        (row, *_) = _get().data["data"]  # type: ignore[attr-defined]
        assert not {"price", "fixed_price", "base_fare", "amount"} & set(row)

    def test_no_internal_identifier_is_published(self) -> None:
        """§7.2: sequential integers never leave the database."""
        _seed()
        (row, *_) = _get().data["data"]  # type: ignore[attr-defined]
        assert set(row) == {
            "id",
            "code",
            "name",
            "description",
            "seats",
            "luggage_capacity",
            "has_air_conditioning",
        }

    def test_the_identifier_is_the_public_uuid(self) -> None:
        classes = _seed()
        (row, *_) = _get().data["data"]  # type: ignore[attr-defined]
        assert str(row["id"]) == str(classes["STANDARD"].public_id)


class TestWithdrawnClassesAreNotOffered:
    def test_an_inactive_class_is_absent(self) -> None:
        """An administrator retiring MINIBUS must stop it being offered without
        deleting the row that priced past bookings."""
        classes = _seed()
        classes["MINIBUS"].is_active = False
        classes["MINIBUS"].save(update_fields=["is_active"])
        assert "MINIBUS" not in [r["code"] for r in _get().data["data"]]  # type: ignore[attr-defined]

    def test_a_soft_deleted_class_is_absent(self) -> None:
        classes = _seed()
        classes["VAN"].delete()
        assert "VAN" not in [r["code"] for r in _get().data["data"]]  # type: ignore[attr-defined]

    def test_an_empty_table_is_an_empty_list_not_an_error(self) -> None:
        """A checkout with no transport seed loaded is a legitimate state, and
        it should read as "we run no transfers yet" rather than as a 500."""
        response = _get()
        assert response.status_code == 200  # type: ignore[attr-defined]
        assert response.data["data"] == []  # type: ignore[attr-defined]
