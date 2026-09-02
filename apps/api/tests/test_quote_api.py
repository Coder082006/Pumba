"""`POST /api/v1/trips/{id}/quote` over HTTP — SRS §9.4.5, §30.3, §9.6.

This file lives under `tests/` rather than `apps/booking/tests/` for the reason
`administrators.py` gives and `test_trip_api.py` repeats: §6.4 forbids
`booking` from importing `identity`, and `apps.booking.tests` is inside
`apps.booking`. A test that needs a *signed-in* tourist spans both modules and
belongs outside both.

`apps/booking/tests/test_quote.py` covers the use case; what is left to prove
is what only the wire can show — the status codes, the error envelope §9.2
builds, the throttle, and §30.3's *"a foreign principal receives 404, not
403"*, which is the one property a service test cannot demonstrate because a
service raises an exception rather than choosing a status.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking.tests import scenario
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _url(public_id: object) -> str:
    return reverse("v1:booking:trip-quote", kwargs={"public_id": public_id})


def _signed_in() -> tuple[APIClient, int]:
    """A signed-in tourist, and the `tourist_profile.id` a trip is owned by.

    Through `signed_in_as` rather than by minting a token: a hand-made token
    skips email verification and the §30.2 obligations, and a suite that
    authenticated differently from production would prove nothing about
    whether this endpoint is reachable.
    """
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    return client, int(profile.id)


def _case(**kwargs: object) -> tuple[APIClient, scenario.Scenario]:
    """A signed-in tourist and a quotable trip they own."""
    client, tourist_id = _signed_in()
    return client, scenario.build(tourist_id=tourist_id, **kwargs)  # type: ignore[arg-type]


class TestTheHappyPath:
    def test_it_answers_200(self) -> None:
        client, case = _case()
        assert client.post(_url(case.trip_public_id)).status_code == 200

    def test_it_returns_the_token_the_clock_and_the_totals(self) -> None:
        """§9.4.5's response: the cost breakdown, `quote_expires_at` and a
        `quote_token` presented at confirmation."""
        client, case = _case(adults=2, price_per_person="95.00")
        body = client.post(_url(case.trip_public_id)).data["data"]
        assert body["quote_token"]
        assert body["expires_at"]
        assert body["subtotal_amount"] == "190.00"
        assert body["status"] == "PRICED"
        assert body["held_seats"] == 2


class TestItRefusesWhatItCannotSell:
    def test_a_sold_out_departure_is_a_409(self) -> None:
        client, case = _case(adults=2, capacity=1, capacity_sold=1)
        assert client.post(_url(case.trip_public_id)).status_code == 409

    def test_the_envelope_carries_the_code_and_the_details(self) -> None:
        """§9.2's error envelope and §9.4.5's `details` array, over the wire.
        A client renders "the 09:00 snorkelling trip is full — try 13:30" from
        this and nothing else."""
        client, case = _case(adults=2, capacity=1, capacity_sold=1)
        body = client.post(_url(case.trip_public_id)).data
        assert body["error"]["code"] == "INVENTORY_UNAVAILABLE"
        assert body["error"]["details"][0]["reason"] == "SOLD_OUT"

    def test_an_unplanned_trip_is_a_409(self) -> None:
        client, case = _case(generated=False)
        response = client.post(_url(case.trip_public_id))
        assert response.status_code == 409
        assert response.data["error"]["code"] == "TRIP_NOT_QUOTABLE"


class TestAuthorisation:
    def test_a_foreign_principal_receives_404_not_403(self) -> None:
        """§30.3. A 403 would confirm the trip exists, for every id an
        attacker cares to try — which is the disclosure the rule prevents.
        Ownership is a `WHERE` clause, so there is no branch that could answer
        403."""
        _, case = _case()
        stranger, _ = _case()
        assert stranger.post(_url(case.trip_public_id)).status_code == 404

    def test_a_foreign_principal_takes_no_capacity(self) -> None:
        _, case = _case()
        stranger, _ = _case()
        stranger.post(_url(case.trip_public_id))
        departure = django_apps.get_model("inventory", "ActivityDeparture").objects.get(
            id=case.departure_id
        )
        assert departure.capacity_held == 0

    def test_an_anonymous_caller_is_refused(self) -> None:
        case = scenario.build()
        assert APIClient().post(_url(case.trip_public_id)).status_code == 401

    def test_a_trip_that_does_not_exist_is_a_404(self) -> None:
        client, _ = _case()
        import uuid

        response = client.post(_url(uuid.uuid4()))
        assert response.status_code == 404


class TestItIsThrottled:
    def test_the_bucket_is_the_trip_rather_than_the_caller(self) -> None:
        """§9.6: *20 / hour / trip*. A tourist planning three trips in an
        afternoon is doing something legitimate; re-quoting one trip forty
        times an hour takes row locks other tourists are queueing behind."""
        from apps.common.config import get_setting
        from apps.common.throttling import parse_limit

        count, seconds, scope = parse_limit(str(get_setting("ratelimit.trip_quote")))
        assert (count, seconds, scope) == (20, 3600, "trip")

    def test_the_limit_is_enforced(self) -> None:
        client, case = _case(capacity=12)
        statuses = [client.post(_url(case.trip_public_id)).status_code for _ in range(21)]
        assert statuses[-1] == 429

    def test_another_trip_is_unaffected(self) -> None:
        """The point of bucketing by trip rather than by principal."""
        client, case = _case(capacity=12)
        elsewhere, other = _case(capacity=12)
        for _ in range(21):
            client.post(_url(case.trip_public_id))
        assert elsewhere.post(_url(other.trip_public_id)).status_code == 200


class TestTheItineraryIsUnchangedByGenerating:
    def test_a_generate_does_not_move_an_activity_off_its_departure(self) -> None:
        """Load-bearing as of Phase 5, and worth proving rather than reading.

        The quote resolves a departure from an item's `starts_at`, so anything
        that moved that instant would silently unbind it. §10.4 treats an item
        with a `starts_at` as *fixed* and only places flexible ones — but that
        is a property of the sequencer, and this is the assertion that keeps
        it true.
        """
        from apps.booking import services
        from apps.trip import services as trip_services

        case = scenario.build()
        trip_services.generate_itinerary(case.trip_public_id, tourist_id=case.tourist_id)

        item = django_apps.get_model("trip", "ItineraryItem").objects.get(
            public_id=case.item_public_id
        )
        assert item.starts_at == case.departs_at

        result = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert result.held_seats == 2

    def test_a_quote_survives_a_regenerate(self) -> None:
        """The loop §10.2 calls "review, adjust, repeat"."""
        from apps.booking import services
        from apps.trip import services as trip_services

        case = scenario.build(capacity=6)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        # A regenerate is only legal from DRAFT, so let the quote lapse first.
        trip_services.expire_quote(case.trip_id)
        trip_services.generate_itinerary(case.trip_public_id, tourist_id=case.tourist_id)

        again = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert again.trip.status == "PRICED"


class TestTheDocumentedContract:
    def test_the_route_is_named_under_the_module_that_implements_it(self) -> None:
        """§37.2's authorisation matrix enumerates endpoints by name. A matrix
        listing this under `trip` would describe a file that does not exist."""
        case = scenario.build()
        assert _url(case.trip_public_id).endswith("/quote")

    def test_expiry_is_reported_as_an_instant_the_client_can_count_down_to(
        self,
    ) -> None:
        client, case = _case()
        body = client.post(_url(case.trip_public_id)).data["data"]
        parsed = dt.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
