"""`Idempotency-Key` on `POST /trips/{id}/quote` — SRS §9.4.5, §9.1, principle A6.

    §9.4.5: "Auth: tourist, owner of the trip. Idempotency-Key: required."
    §9.1:   "Idempotency-Key header required on all POST that create bookings,
             payments or assignments; server stores key -> response for 24 h."

**Why this endpoint and not only Phase 7's.** §9.4.5 names the header itself,
and the quote is where a duplicate costs something a booking POST does not: it
takes row locks on shared counters, increments `capacity_held`, and starts a
twenty-minute clock. A client whose request timed out and retried would hold a
second set of seats against the same trip — seats no other tourist can buy —
and be handed a second offer while believing it had one.

The interesting assertions here are the ones about *capacity*, not about
response bodies. A replay cache that returned the right JSON while running the
handler twice would satisfy every naive test and none of these.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.db import connections
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking.tests import scenario
from apps.common.idempotency import IdempotencyRecord
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _url(public_id: object) -> str:
    return reverse("v1:booking:trip-quote", kwargs={"public_id": public_id})


def _case(**kwargs: Any) -> tuple[APIClient, Any]:
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    return client, scenario.build(tourist_id=int(profile.id), **kwargs)


def _post(client: APIClient, public_id: object, key: str | None) -> Any:
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
    return client.post(_url(public_id), **headers)


def _held(case: Any) -> int:
    return int(
        django_apps.get_model("inventory", "ActivityDeparture")
        .objects.get(id=case.departure_id)
        .capacity_held
    )


class TestTheHeaderIsRequired:
    def test_a_request_without_it_is_refused(self) -> None:
        """§9.4.5 says required, so absent is an error and not a default.

        An endpoint that accepted the header when offered and ignored its
        absence would give a client no way to discover its retries were
        unprotected — which is precisely the state this endpoint was in until
        this commit.
        """
        client, case = _case()
        response = _post(client, case.trip_public_id, None)
        assert response.status_code == 422
        assert response.data["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    def test_the_refusal_names_the_header(self) -> None:
        client, case = _case()
        response = _post(client, case.trip_public_id, None)
        assert response.data["error"]["details"][0]["field"] == "Idempotency-Key"

    def test_a_blank_key_is_not_a_key(self) -> None:
        client, case = _case()
        assert _post(client, case.trip_public_id, "   ").status_code == 422

    def test_nothing_is_held_when_the_header_is_missing(self) -> None:
        """The refusal happens before the handler, not after it."""
        client, case = _case()
        _post(client, case.trip_public_id, None)
        assert _held(case) == 0

    def test_an_over_long_key_is_refused_rather_than_truncated(self) -> None:
        """The column holds 64 characters. Silently shortening a key would
        make it collide with every other key sharing its first 64."""
        client, case = _case()
        response = _post(client, case.trip_public_id, "k" * 65)
        assert response.status_code == 422
        assert _held(case) == 0


class TestARetryDoesNotQuoteTwice:
    def test_the_same_key_returns_the_same_response(self) -> None:
        client, case = _case(adults=2, capacity=12)
        key = uuid.uuid4().hex
        first = _post(client, case.trip_public_id, key)
        second = _post(client, case.trip_public_id, key)

        assert (first.status_code, second.status_code) == (200, 200)
        assert first.data == second.data

    def test_the_quote_token_is_the_first_one(self) -> None:
        """Not merely an equal body — the *same* offer.

        A second quote would mint a fresh token and a fresh clock, so a client
        that retried would be holding an offer whose predecessor it had already
        shown the tourist.
        """
        client, case = _case(adults=2, capacity=12)
        key = uuid.uuid4().hex
        first = _post(client, case.trip_public_id, key).data["data"]
        second = _post(client, case.trip_public_id, key).data["data"]
        assert first["quote_token"] == second["quote_token"]
        assert first["expires_at"] == second["expires_at"]

    def test_no_second_set_of_seats_is_held(self) -> None:
        """The assertion the whole mechanism exists for.

        A re-quote releases the trip's own prior holds first, so even an
        unprotected retry would not *double* the count — it would release two
        seats and take two more. What it would do is take fresh locks, restart
        the clock, and issue a competing offer. The count is asserted anyway,
        because it is the number that must never drift.
        """
        client, case = _case(adults=2, capacity=12)
        key = uuid.uuid4().hex
        _post(client, case.trip_public_id, key)
        assert _held(case) == 2
        _post(client, case.trip_public_id, key)
        assert _held(case) == 2

    def test_only_one_hold_row_exists(self) -> None:
        """Where an unprotected replay would show, if it happened.

        `inventory_hold` rows are the ledger of what the quote did. One live
        row per departure per trip is what a single quote leaves behind.
        """
        client, case = _case(adults=2, capacity=12)
        key = uuid.uuid4().hex
        _post(client, case.trip_public_id, key)
        _post(client, case.trip_public_id, key)

        holds = django_apps.get_model("inventory", "InventoryHold").objects.filter(status="HELD")
        assert holds.count() == 1

    def test_a_different_key_does_quote_again(self) -> None:
        """The control. Without it every assertion above would also pass on an
        endpoint that had simply stopped working."""
        client, case = _case(adults=2, capacity=12)
        first = _post(client, case.trip_public_id, uuid.uuid4().hex).data["data"]
        second = _post(client, case.trip_public_id, uuid.uuid4().hex).data["data"]
        assert first["quote_token"] != second["quote_token"]


class TestAKeyBelongsToOneOperation:
    def test_the_same_key_on_another_trip_is_a_different_operation(self) -> None:
        """The bug an empty request body cannot catch.

        The fingerprint hashes the body, and this endpoint's body is empty — so
        two different trips fingerprint identically. What distinguishes them is
        that the key is scoped by the request *path*. Scoping by the route name
        would have replayed the first trip's quote as the second trip's, which
        is a wrong answer rather than an error.
        """
        client, first = _case(capacity=12)
        profile = (
            django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
        )
        assert profile is not None
        second = scenario.build(tourist_id=int(profile.id))

        key = uuid.uuid4().hex
        one = _post(client, first.trip_public_id, key)
        two = _post(client, second.trip_public_id, key)

        assert one.status_code == 200
        assert two.status_code == 200
        assert one.data["data"]["quote_token"] != two.data["data"]["quote_token"]

    def test_one_principals_key_cannot_replay_anothers(self) -> None:
        """Scoped by principal as well as by path.

        Otherwise a client could guess a key and be handed somebody else's
        quote — a stranger's trip reference, totals and token.
        """
        owner, case = _case(capacity=12)
        stranger, _ = _case()

        key = uuid.uuid4().hex
        assert _post(owner, case.trip_public_id, key).status_code == 200
        # The same key, the same path, a different principal: not a replay, and
        # therefore the ordinary §30.3 answer for a trip that is not theirs.
        assert _post(stranger, case.trip_public_id, key).status_code == 404


class TestAFailedAttemptReleasesItsKey:
    def test_a_409_is_not_replayed(self) -> None:
        """§9.1 says the response is stored; storing *failures* is the reading
        that does harm.

        `INVENTORY_UNAVAILABLE` is a statement about the world at one instant.
        Serving it again for twenty-four hours would tell a tourist a departure
        is full long after the sweeper released the seats — and the retry that
        would have succeeded never reaches the handler.
        """
        client, case = _case(adults=2, capacity=1, capacity_sold=1)
        key = uuid.uuid4().hex
        assert _post(client, case.trip_public_id, key).status_code == 409

        # The world changes: the seat comes back.
        departure_model = django_apps.get_model("inventory", "ActivityDeparture")
        departure_model.objects.filter(id=case.departure_id).update(
            capacity_total=12, capacity_sold=0
        )

        assert _post(client, case.trip_public_id, key).status_code == 200

    def test_a_failure_leaves_no_record_behind(self) -> None:
        client, case = _case(adults=2, capacity=1, capacity_sold=1)
        _post(client, case.trip_public_id, uuid.uuid4().hex)
        assert not IdempotencyRecord.objects.exists()

    def test_a_success_does_leave_one(self) -> None:
        client, case = _case(capacity=12)
        _post(client, case.trip_public_id, uuid.uuid4().hex)
        record = IdempotencyRecord.objects.get()
        assert record.response_status == 200
        assert record.endpoint.endswith("/quote")


class TestTheRetentionWindow:
    def test_it_comes_from_a_setting_rather_than_a_literal(self) -> None:
        """Hard rule 5: thresholds are `system_setting` rows. §9.1's twenty-four
        hours is one, and shortening it is how the table is made to shed rows
        under load without a deployment."""
        from apps.common.config import get_setting

        assert int(get_setting("idempotency.retention_hours")) == 24

    def test_an_expired_record_does_not_replay(self) -> None:
        """A retention window, not a lifetime lease on the key."""
        import datetime as dt

        from django.utils import timezone

        client, case = _case(capacity=12)
        key = uuid.uuid4().hex
        first = _post(client, case.trip_public_id, key).data["data"]

        IdempotencyRecord.objects.update(expires_at=timezone.now() - dt.timedelta(seconds=1))

        second = _post(client, case.trip_public_id, key)
        assert second.status_code == 200
        assert second.data["data"]["quote_token"] != first["quote_token"]


@pytest.mark.django_db(transaction=True)
class TestTwoRequestsAtOnce:
    """The case the mechanism exists for, and the one a replay cache misses.

    A client whose request timed out retries while the first is still running.
    A store written only on the way out would have nothing to find, both would
    execute, and the second would take locks behind the first.

    Reserving the key *before* the handler makes the unique constraint the
    thing that serialises them: exactly one runs, and the other is told so.
    """

    def test_exactly_one_of_two_simultaneous_requests_quotes(self) -> None:
        client, case = _case(adults=2, capacity=12)
        key = uuid.uuid4().hex
        start = threading.Barrier(2, timeout=15)
        results: list[int] = []
        lock = threading.Lock()

        def attempt() -> None:
            try:
                start.wait()
                status = _post(client, case.trip_public_id, key).status_code
                with lock:
                    results.append(status)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert len(results) == 2, results
        # One ran. The other either lost the race to reserve the key and was
        # told the first was in flight, or arrived after it finished and got
        # the replay. Both are correct; two quotes are not.
        assert sorted(results) in ([200, 200], [200, 409]), results
        assert _held(case) == 2

        holds = django_apps.get_model("inventory", "InventoryHold").objects.filter(status="HELD")
        assert holds.count() == 1
