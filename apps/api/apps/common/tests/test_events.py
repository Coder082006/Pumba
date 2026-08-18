"""Domain event bus tests — SRS §8.3, §8.9.

The after-commit guarantee is the reason this module exists, so it is what
these tests concentrate on: no notification, ledger accrual or webhook may
ever describe state that was rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.db import transaction

from apps.common.events import DomainEvent, clear_subscribers, get_subscribers, publish, subscribe


@dataclass(frozen=True, slots=True, kw_only=True)
class BookingConfirmed(DomainEvent):
    name = "booking.confirmed"
    booking_public_id: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentCaptured(DomainEvent):
    name = "payment.captured"


@pytest.fixture(autouse=True)
def _clean_bus():
    clear_subscribers()
    yield
    clear_subscribers()


class TestSubscription:
    def test_subscribe_registers_a_handler(self) -> None:
        def handler(event: BookingConfirmed) -> None: ...

        subscribe(BookingConfirmed, handler)
        assert handler in get_subscribers(BookingConfirmed)

    def test_subscribe_works_as_a_decorator(self) -> None:
        @subscribe(BookingConfirmed)  # type: ignore[arg-type,misc]
        def handler(event: BookingConfirmed) -> None: ...

        assert len(get_subscribers(BookingConfirmed)) == 1

    def test_handlers_are_isolated_by_event_type(self) -> None:
        subscribe(BookingConfirmed, lambda e: None)
        assert get_subscribers(PaymentCaptured) == []


class TestEventIdentity:
    def test_every_event_gets_a_unique_id_and_utc_timestamp(self) -> None:
        first, second = BookingConfirmed(), BookingConfirmed()
        assert first.event_id != second.event_id
        assert first.occurred_at.tzinfo is not None, "SRS §7.2: never a naive datetime"

    def test_as_dict_includes_the_event_name(self) -> None:
        payload = BookingConfirmed(booking_public_id="abc").as_dict()
        assert payload["name"] == "booking.confirmed"
        assert payload["booking_public_id"] == "abc"
        assert isinstance(payload["occurred_at"], str), "must be JSON-serialisable for Celery"


class TestDispatchIsDeferred:
    """The after-commit guarantee, verified without a database.

    The real behaviour needs a real transaction, which is what
    `TestAfterCommitDispatch` below covers. But that suite needs Docker, so
    this one keeps the guarantee under test everywhere: it asserts that
    `publish` hands the dispatch to `transaction.on_commit` rather than
    invoking handlers inline. If someone "simplifies" publish into a direct
    call, this fails immediately rather than in CI an hour later.
    """

    def test_publish_defers_to_transaction_on_commit(self, monkeypatch) -> None:
        captured: list[object] = []
        monkeypatch.setattr(
            "apps.common.events.transaction.on_commit", lambda fn: captured.append(fn)
        )
        received: list[DomainEvent] = []
        subscribe(BookingConfirmed, received.append)

        publish(BookingConfirmed(booking_public_id="x"))

        assert received == [], "handlers ran inline instead of after commit"
        assert len(captured) == 1, "publish did not register an on_commit callback"

        captured[0]()  # type: ignore[operator]
        assert len(received) == 1


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
class TestAfterCommitDispatch:
    """SRS §8.3: "no notification ... can describe a state that was rolled back".

    Needs a real transaction, so a real database. Skipped without Docker.
    """

    def test_handler_does_not_run_before_commit(self) -> None:
        received: list[DomainEvent] = []
        subscribe(BookingConfirmed, received.append)

        with transaction.atomic():
            publish(BookingConfirmed(booking_public_id="x"))
            assert received == [], "handler ran before the transaction committed"

        assert len(received) == 1, "handler did not run after commit"

    def test_handler_never_runs_when_the_transaction_rolls_back(self) -> None:
        received: list[DomainEvent] = []
        subscribe(BookingConfirmed, received.append)

        class RollbackError(Exception):
            pass

        with pytest.raises(RollbackError), transaction.atomic():
            publish(BookingConfirmed(booking_public_id="x"))
            raise RollbackError

        assert received == [], "a rolled-back transaction emitted an event"

    def test_a_failing_handler_does_not_prevent_the_others(self) -> None:
        """SRS §8.8 gives Celery retry ownership of recovery; one bad consumer
        must not silently swallow its siblings."""
        received: list[str] = []

        def explodes(event: DomainEvent) -> None:
            raise RuntimeError("consumer is down")

        subscribe(BookingConfirmed, explodes)
        subscribe(BookingConfirmed, lambda e: received.append("second"))

        with transaction.atomic():
            publish(BookingConfirmed())

        assert received == ["second"]

    def test_publishing_with_no_subscribers_is_harmless(self) -> None:
        with transaction.atomic():
            publish(PaymentCaptured())
