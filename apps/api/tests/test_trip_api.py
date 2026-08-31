"""The trip API over HTTP — SRS §9.4.2, §30.3, §32.

This file lives under `tests/` rather than `apps/trip/tests/` for the reason
`administrators.py` gives: §6.4 forbids `trip` from importing `identity`, and
`apps.trip.tests` is inside `apps.trip`. A test that needs a signed-in tourist
spans both modules and belongs outside both.

**The authorisation assertions are the point of this file.** §30.3 requires a
foreign principal to receive 404 rather than 403, so that absence and
inaccessibility are indistinguishable. The service layer implements that by
putting `tourist_id` in the query; here it is demonstrated per endpoint, and
the strongest assertion is not "not 200" — it is that a stranger's request and
a request for a trip that never existed produce **the same status and the same
body**. A test that only checked the status would pass for an implementation
that leaked existence through the error message.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import make_accommodation, make_activity, make_destination
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

START = date(2027, 6, 1)
END = date(2027, 6, 4)
UNKNOWN = uuid.uuid4()


def at(day: int, hour: int) -> str:
    """An ISO instant on a trip day, in UTC.

    Twelve hours back because the fixture destinations sit in Auckland
    (UTC+12): 10:00 local on day 2 is 22:00 UTC on day 1, and a naive local
    time here would put items on the wrong side of a day boundary.
    """
    return (datetime(2027, 6, day, hour, tzinfo=UTC) - timedelta(hours=12)).isoformat()


def body(response: Any) -> Any:
    return response.json()["data"]


def create_trip(client: APIClient, destination_slug: str, **overrides: Any) -> Any:
    payload = {
        "destination": destination_slug,
        "start_date": START.isoformat(),
        "end_date": END.isoformat(),
        "adults": 2,
    }
    payload.update(overrides)
    response = client.post("/api/v1/trips", payload, format="json")
    assert response.status_code == 201, response.content
    return body(response)


@pytest.fixture
def tourist() -> APIClient:
    return signed_in_as()


@pytest.fixture
def destination() -> Any:
    return make_destination()


class TestCreateAndRead:
    def test_a_trip_is_created_in_draft_with_an_empty_itinerary(
        self, tourist: APIClient, destination: Any
    ) -> None:
        trip = create_trip(tourist, destination.slug)
        assert trip["status"] == "DRAFT"
        assert trip["itinerary"]["version"] == 1
        assert trip["itinerary"]["items"] == []
        assert trip["reference"].startswith("TRP-")

    def test_no_integer_identifier_reaches_the_wire(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """§7.2. The DTO layer already guarantees this and a serializer is the
        one place it could be undone, so it is checked again at the boundary
        that actually faces a client."""
        trip = create_trip(tourist, destination.slug)

        def walk(node: Any) -> list[str]:
            if isinstance(node, dict):
                bad = [k for k in node if (k == "id" or k.endswith("_id")) and k != "public_id"]
                return bad + [x for v in node.values() for x in walk(v)]
            if isinstance(node, list):
                return [x for v in node for x in walk(v)]
            return []

        assert walk(trip) == []

    def test_the_destination_is_named_not_numbered(
        self, tourist: APIClient, destination: Any
    ) -> None:
        trip = create_trip(tourist, destination.slug)
        assert trip["destination"]["slug"] == destination.slug
        assert trip["destination"]["name"] == destination.name

    def test_my_trips_lists_only_mine(self, tourist: APIClient, destination: Any) -> None:
        mine = create_trip(tourist, destination.slug)
        create_trip(signed_in_as(), destination.slug)

        listed = body(tourist.get("/api/v1/trips"))
        assert [t["public_id"] for t in listed] == [mine["public_id"]]

    def test_the_list_is_narrower_than_the_detail(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """§24.20's list does not render an itinerary. Returning the detail
        shape would teach clients to depend on fields it will later stop
        loading."""
        create_trip(tourist, destination.slug)
        row = body(tourist.get("/api/v1/trips"))[0]
        assert "itinerary" not in row
        assert "flights" not in row


class TestAuthorisation:
    """§30.3: 404, never 403, and the same 404 either way."""

    def _endpoints(self, trip_id: str) -> list[tuple[str, str, Any]]:
        return [
            ("get", f"/api/v1/trips/{trip_id}", None),
            ("patch", f"/api/v1/trips/{trip_id}", {"title": "theirs"}),
            (
                "post",
                f"/api/v1/trips/{trip_id}/items",
                # A complete, valid body. An incomplete one is rejected as 422
                # by the serializer before ownership is ever consulted, which
                # is correct — the answer then depends only on the body and
                # discloses nothing — but it would make this test assert the
                # wrong thing.
                {
                    "item_type": "FREE_TIME",
                    "day_number": 1,
                    "sequence_no": 1,
                    "title": "Beach",
                    "starts_at": at(1, 9),
                    "ends_at": at(1, 10),
                },
            ),
            ("patch", f"/api/v1/trips/{trip_id}/items/{UNKNOWN}", {"title": "x"}),
            ("delete", f"/api/v1/trips/{trip_id}/items/{UNKNOWN}", None),
            ("put", f"/api/v1/trips/{trip_id}/flights", {"flights": []}),
            ("post", f"/api/v1/trips/{trip_id}/itinerary/generate", None),
            ("post", f"/api/v1/trips/{trip_id}/cancel", None),
        ]

    def test_every_endpoint_refuses_a_stranger_with_404(
        self, tourist: APIClient, destination: Any
    ) -> None:
        trip = create_trip(tourist, destination.slug)
        stranger = signed_in_as()

        for method, url, payload in self._endpoints(trip["public_id"]):
            call = getattr(stranger, method)
            response = call(url, payload, format="json") if payload else call(url)
            assert response.status_code == 404, f"{method.upper()} {url} -> {response.status_code}"

    def test_a_stranger_and_a_nonexistent_trip_are_indistinguishable(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """The assertion that actually enforces §30.3.

        Checking only the status would pass for an implementation whose 404
        message said "you do not own this trip" — which discloses exactly what
        the rule exists to hide. So the bodies are compared, with the request
        id (which differs per request by design) removed.
        """
        trip = create_trip(tourist, destination.slug)
        stranger = signed_in_as()

        foreign = stranger.get(f"/api/v1/trips/{trip['public_id']}")
        imaginary = stranger.get(f"/api/v1/trips/{UNKNOWN}")

        assert foreign.status_code == imaginary.status_code == 404

        def comparable(response: Any) -> Any:
            payload = response.json()
            error = payload["error"]
            # `request_id` differs per request by design, and the message
            # names the id that was asked for — which is the caller's own
            # input, not a fact about what exists.
            error.pop("request_id", None)
            error.pop("message", None)
            return payload

        assert comparable(foreign) == comparable(imaginary)

    def test_a_stranger_changes_nothing(self, tourist: APIClient, destination: Any) -> None:
        trip = create_trip(tourist, destination.slug)
        signed_in_as().patch(
            f"/api/v1/trips/{trip['public_id']}", {"title": "theirs"}, format="json"
        )
        assert body(tourist.get(f"/api/v1/trips/{trip['public_id']}"))["title"] is None

    def test_an_anonymous_request_is_401_not_404(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """Absence is hidden from a principal who is not the owner. It is not
        hidden from the question of being signed in at all — answering 404
        there would tell an anonymous caller nothing useful and would make
        every unauthenticated bug look like a routing error."""
        trip = create_trip(tourist, destination.slug)
        assert APIClient().get(f"/api/v1/trips/{trip['public_id']}").status_code == 401


class TestTheRoundTrip:
    def test_a_generated_itinerary_carries_its_transfer_and_its_label(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """The whole phase, end to end over HTTP.

        The `estimate_quality` assertion is the one that matters: §12.6
        requires the UI to render an explicit "approximate" label, and the
        serializer is the last place that label can be lost.
        """
        trip = create_trip(tourist, destination.slug)
        trip_id = trip["public_id"]
        accommodation_id = make_accommodation(destination=destination).id
        activity = make_activity(destination=destination)

        tourist.post(
            f"/api/v1/trips/{trip_id}/items",
            {
                "item_type": "STAY",
                "day_number": 1,
                "sequence_no": 1,
                "title": "Harbourside Lodge",
                "starts_at": at(1, 14),
                "ends_at": at(4, 10),
                "accommodation_id": accommodation_id,
            },
            format="json",
        )
        tourist.post(
            f"/api/v1/trips/{trip_id}/items",
            {
                "item_type": "ACTIVITY",
                # The same day as the 14:00 check-in. §10.4 sequences within a
                # day and a stay appears only on the days it begins and ends,
                # so an activity on a middle day has nothing to be adjacent to
                # and no transfer is inserted to reach it.
                "day_number": 1,
                "sequence_no": 2,
                "title": "Harbour Kayak Tour",
                "starts_at": at(1, 16),
                "ends_at": at(1, 19),
                "activity_id": activity.id,
            },
            format="json",
        )

        generated = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))
        transfers = [i for i in generated["itinerary"]["items"] if i["item_type"] == "TRANSFER"]
        assert transfers, "no transfer was inserted between two different places"
        assert transfers[0]["estimate_quality"] == "APPROXIMATE"
        assert transfers[0]["is_approximate"] is True
        assert transfers[0]["travel_seconds"] > 0

    def test_a_transfer_carries_no_price_over_the_wire(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """§10.7 sources a transfer's line total from §12.4's tariff, which is
        Phase 6. Null rather than zero: zero would read as free."""
        trip = create_trip(tourist, destination.slug)
        trip_id = trip["public_id"]
        activity = make_activity(destination=destination)
        tourist.post(
            f"/api/v1/trips/{trip_id}/items",
            {
                "item_type": "ACTIVITY",
                "day_number": 1,
                "sequence_no": 1,
                "title": "Kayak",
                "starts_at": at(1, 10),
                "ends_at": at(1, 13),
                "activity_id": activity.id,
            },
            format="json",
        )
        generated = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))
        for item in generated["itinerary"]["items"]:
            if item["item_type"] == "TRANSFER":
                assert item["line_total"] is None

    def test_findings_are_part_of_the_success_response(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """§10.6 returns findings from a generate that worked; they are not an
        error channel. A trip with no stay warns VR-16 and still succeeds."""
        trip = create_trip(tourist, destination.slug)
        response = tourist.post(f"/api/v1/trips/{trip['public_id']}/itinerary/generate")
        assert response.status_code == 200
        codes = [f["code"] for f in body(response)["itinerary"]["findings"]]
        assert "VR-16" in codes


class TestErrors:
    def test_an_unaddable_item_type_is_422(self, tourist: APIClient, destination: Any) -> None:
        """§10.4 inserts transfers; one written by hand would be rewritten by
        the next generate."""
        trip = create_trip(tourist, destination.slug)
        response = tourist.post(
            f"/api/v1/trips/{trip['public_id']}/items",
            {
                "item_type": "TRANSFER",
                "day_number": 1,
                "sequence_no": 1,
                "title": "My own leg",
                "starts_at": at(1, 9),
                "ends_at": at(1, 10),
            },
            format="json",
        )
        assert response.status_code == 422

    def test_a_trip_longer_than_the_maximum_is_422(
        self, tourist: APIClient, destination: Any
    ) -> None:
        response = tourist.post(
            "/api/v1/trips",
            {
                "destination": destination.slug,
                "start_date": START.isoformat(),
                "end_date": (START + timedelta(days=365)).isoformat(),
                "adults": 2,
            },
            format="json",
        )
        assert response.status_code == 422

    def test_an_unknown_destination_is_404(self, tourist: APIClient) -> None:
        response = tourist.post(
            "/api/v1/trips",
            {
                "destination": "no-such-place",
                "start_date": START.isoformat(),
                "end_date": END.isoformat(),
            },
            format="json",
        )
        assert response.status_code == 404

    def test_the_error_envelope_is_the_platform_s(
        self, tourist: APIClient, destination: Any
    ) -> None:
        """§9.2, §32: one envelope, with a machine-readable code.

        `request_id` sits inside `error` rather than beside it in `meta` —
        `meta` belongs to the success envelope. Asserted rather than assumed,
        because a client correlating a failure to a log line reads this field.
        """
        response = tourist.get(f"/api/v1/trips/{UNKNOWN}")
        payload = response.json()
        assert payload["error"]["code"] == "NOT_FOUND"
        assert payload["error"]["request_id"]
        assert "meta" not in payload


class TestCancel:
    def test_a_draft_trip_cancels(self, tourist: APIClient, destination: Any) -> None:
        trip = create_trip(tourist, destination.slug)
        cancelled = body(tourist.post(f"/api/v1/trips/{trip['public_id']}/cancel"))
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["cancelled_at"] is not None

    def test_cancelling_twice_is_409(self, tourist: APIClient, destination: Any) -> None:
        trip = create_trip(tourist, destination.slug)
        tourist.post(f"/api/v1/trips/{trip['public_id']}/cancel")
        assert tourist.post(f"/api/v1/trips/{trip['public_id']}/cancel").status_code == 409
