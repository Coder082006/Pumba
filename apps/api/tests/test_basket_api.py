"""`POST /api/v1/trips/{id}/confirm` over HTTP — SRS §9.4.6, §30.3, §9.1.

Under `tests/` for the reason `test_quote_api.py` gives: a signed-in tourist
spans `identity` and `booking`, and §6.4 lets neither module's tests import the
other.

`apps/booking/tests/test_basket.py` covers TC-060 and TC-061 as a use case.
What only the wire can show is here: the 201, the shape of the basket, the
`Idempotency-Key` contract (§9.1, hard rule 9), and §30.3's "a foreign
principal receives 404, not 403" — and that the stranger leaves nothing behind.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking.models import Booking
from apps.booking.tests import scenario
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _signed_in() -> tuple[APIClient, int]:
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    return client, int(profile.id)


def _quote(client: APIClient, public_id: object) -> dict[str, Any]:
    response = client.post(
        reverse("v1:booking:trip-quote", kwargs={"public_id": public_id}),
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 200, response.data
    data: dict[str, Any] = response.data["data"]
    return data


def _confirm(client: APIClient, public_id: object, token: object, *, key: str | None = None) -> Any:
    headers = {} if key == "" else {"HTTP_IDEMPOTENCY_KEY": key or uuid.uuid4().hex}
    return client.post(
        reverse("v1:booking:trip-confirm", kwargs={"public_id": public_id}),
        {"quote_token": str(token), "payment_method": "CARD"},
        format="json",
        **headers,
    )


def _case(**kwargs: object) -> tuple[APIClient, scenario.Scenario, dict[str, Any]]:
    client, tourist_id = _signed_in()
    built = scenario.build(tourist_id=tourist_id, **kwargs)  # type: ignore[arg-type]
    return client, built, _quote(client, built.trip_public_id)


class TestTheHappyPath:
    def test_it_answers_201_with_the_basket(self) -> None:
        client, built, quote = _case(adults=2, price_per_person="95.00")
        response = _confirm(client, built.trip_public_id, quote["quote_token"])

        assert response.status_code == 201, response.data
        body = response.data["data"]
        assert body["trip_status"] == "PENDING_PAYMENT"
        assert body["total_amount"] == quote["total_amount"]
        assert body["payment_expires_at"]
        [booking] = body["bookings"]
        assert booking["status"] == "PENDING"
        assert booking["reference"].startswith("BKG-")
        assert booking["gross_amount"] == "190.00"
        assert booking["cancellation_policy_code"] == "MODERATE_7D"

    def test_no_integer_id_reaches_the_client(self) -> None:
        """Hard rule 8: the booking is addressed by its `public_id`."""
        client, built, quote = _case()
        booking = _confirm(client, built.trip_public_id, quote["quote_token"]).data["data"][
            "bookings"
        ][0]
        assert "provider_id" not in booking and "trip_id" not in booking
        uuid.UUID(booking["id"])


class TestTC061OverTheWire:
    def test_a_wrong_token_is_409_quote_expired_and_creates_nothing(self) -> None:
        client, built, _ = _case()
        response = _confirm(client, built.trip_public_id, uuid.uuid4())
        assert response.status_code == 409
        assert response.data["error"]["code"] == "QUOTE_EXPIRED"
        assert not Booking.objects.exists()


class TestIdempotency:
    """§9.1 and hard rule 9: a mutating POST creating a booking carries a key."""

    def test_the_key_is_required(self) -> None:
        client, built, quote = _case()
        response = _confirm(client, built.trip_public_id, quote["quote_token"], key="")
        assert response.status_code == 422
        assert response.data["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        assert not Booking.objects.exists()

    def test_a_retry_replays_the_first_basket_and_books_nothing_twice(self) -> None:
        client, built, quote = _case()
        first = _confirm(client, built.trip_public_id, quote["quote_token"], key="retry-me")
        second = _confirm(client, built.trip_public_id, quote["quote_token"], key="retry-me")

        assert (first.status_code, second.status_code) == (201, 201)
        assert second.data == first.data
        assert Booking.objects.count() == 1

    def test_a_second_attempt_with_a_new_key_is_refused(self) -> None:
        client, built, quote = _case()
        _confirm(client, built.trip_public_id, quote["quote_token"])
        again = _confirm(client, built.trip_public_id, quote["quote_token"])
        assert again.status_code == 409
        assert again.data["error"]["code"] == "TRIP_NOT_PAYABLE"
        assert Booking.objects.count() == 1


class TestAuthorisation:
    def test_a_foreign_tourist_gets_404_not_403(self) -> None:
        """§30.3. A stranger cannot tell this trip from one that does not exist."""
        _, built, quote = _case()
        stranger, _ = _signed_in()
        response = _confirm(stranger, built.trip_public_id, quote["quote_token"])
        assert response.status_code == 404
        assert not Booking.objects.exists()

    def test_a_nonexistent_trip_is_the_same_404(self) -> None:
        client, _, quote = _case()
        response = _confirm(client, uuid.uuid4(), quote["quote_token"])
        assert response.status_code == 404

    def test_an_administrator_is_not_a_tourist(self) -> None:
        from apps.common.authz import Role

        _, built, quote = _case()
        response = _confirm(
            signed_in_as(Role.SUPER_ADMIN), built.trip_public_id, quote["quote_token"]
        )
        assert response.status_code in {403, 404}
        assert not Booking.objects.exists()

    def test_anonymous_is_401(self) -> None:
        _, built, quote = _case()
        response = _confirm(APIClient(), built.trip_public_id, quote["quote_token"])
        assert response.status_code == 401


class TestUnsellableComponents:
    def test_an_unverified_seller_is_409_naming_the_item(self) -> None:
        client, built, quote = _case(seller_status="SUBMITTED")
        response = _confirm(client, built.trip_public_id, quote["quote_token"])
        assert response.status_code == 409
        error = response.data["error"]
        assert error["code"] == "NOT_BOOKABLE"
        assert error["details"][0]["item"] == str(built.item_public_id)
