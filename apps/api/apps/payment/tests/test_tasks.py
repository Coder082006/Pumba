"""§21.5's polling fallback — the half that runs when no webhook arrives.

The ladder is the specification's, and the reason this file checks arithmetic
rather than only behaviour: §21.5 gives the probes as offsets from the intent
(30 s, 2 min, 5 min, 10 min, 20 min, 30 min) and Celery takes gaps, so the
conversion is exactly where a payment quietly stops being chased.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from apps.common.money import Money
from apps.common.ports_registry import get_payment_port
from apps.payment import services, tasks
from apps.payment.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    PaymentWebhookEvent,
    WebhookOutcome,
)
from ports.payment import PaymentMethod as PortMethod

pytestmark = pytest.mark.django_db


def a_payment(**overrides: object) -> Payment:
    fields: dict[str, object] = {
        "trip_id": 1,
        "tourist_id": 1,
        "method": PaymentMethod.CARD,
        "presentment_currency": "USD",
        "presentment_amount": Decimal("10.00"),
        "settlement_currency": "USD",
        "psp_name": "stripe",
        "idempotency_key": uuid.uuid4().hex,
        "status": PaymentStatus.PENDING,
        "psp_reference": f"pi_{uuid.uuid4().hex[:8]}",
    }
    fields.update(overrides)
    return Payment.objects.create(**fields)


def _at_the_gateway() -> Payment:
    """A payment the fake gateway has actually heard of.

    `poll_payment` asks the port about a reference, so a row carrying an
    invented one would test the error path rather than the polling path.
    """
    payment = a_payment()
    intent = get_payment_port().create_intent(
        amount=Money(Decimal("10.00"), "USD"),
        method=PortMethod.CARD,
        idempotency_key=uuid.uuid4().hex,
    )
    payment.psp_reference = intent.psp_reference
    payment.save(update_fields=["psp_reference"])
    return payment


class TestTheLadder:
    def test_it_is_section_215s_offsets(self) -> None:
        assert tasks.PROBE_SCHEDULE == (30, 120, 300, 600, 1200, 1800)

    def test_each_rung_waits_the_difference_not_the_offset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The conversion that decides whether the sixth probe happens half an
        hour after the intent or two and a half hours after it."""
        scheduled: list[dict[str, Any]] = []
        monkeypatch.setattr(services, "poll_payment", lambda payment_id: "PENDING")
        monkeypatch.setattr(
            tasks.verify_payment_status,
            "apply_async",
            lambda *args, **kwargs: scheduled.append(kwargs),
        )
        payment = a_payment()

        for attempt in range(len(tasks.PROBE_SCHEDULE) - 1):
            tasks.verify_payment_status(int(payment.pk), attempt)

        assert [entry["countdown"] for entry in scheduled] == [90, 180, 300, 600, 600]
        assert sum(entry["countdown"] for entry in scheduled) == 1800 - 30

    def test_the_last_rung_gives_up_rather_than_scheduling_forever(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§8.8: "6 attempts over 30 min, then mark for review"."""
        monkeypatch.setattr(services, "poll_payment", lambda payment_id: "PENDING")
        monkeypatch.setattr(
            tasks.verify_payment_status,
            "apply_async",
            lambda *args, **kwargs: pytest.fail("scheduled a seventh probe"),
        )
        payment = a_payment()

        assert tasks.verify_payment_status(int(payment.pk), len(tasks.PROBE_SCHEDULE) - 1) == (
            "FOR_REVIEW"
        )

    def test_a_finished_payment_stops_the_ladder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            tasks.verify_payment_status,
            "apply_async",
            lambda *args, **kwargs: pytest.fail("kept polling a terminal payment"),
        )
        payment = a_payment(status=PaymentStatus.FAILED)

        assert tasks.verify_payment_status(int(payment.pk)) == "TERMINAL"


class TestPolling:
    def test_a_payment_that_is_gone_is_terminal_rather_than_an_error(self) -> None:
        """A task holding a stale id must not raise: Celery would retry it on a
        schedule that is not §21.5's."""
        assert services.poll_payment(999_999) == "TERMINAL"

    def test_a_payment_with_no_reference_is_not_asked_about(self) -> None:
        """`initiate` never got an answer from the gateway, so there is nothing
        to ask — and asking with an empty reference would be a 404 from Stripe
        every thirty seconds."""
        payment = a_payment(psp_reference=None, status=PaymentStatus.INITIATED)

        assert services.poll_payment(int(payment.pk)) == "PENDING"

    def test_it_applies_what_the_gateway_says(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The point of the fallback: the same interpretation the webhook would
        have applied, reached without one."""
        seen: list[str] = []
        monkeypatch.setattr(
            services,
            "apply_psp_state",
            lambda *, event, stored_event_id=None: (
                seen.append(event.event_type) or WebhookOutcome.APPLIED
            ),
        )
        payment = _at_the_gateway()

        assert services.poll_payment(int(payment.pk)) == "APPLIED"
        assert seen == ["payment.polled"]

    def test_a_poll_writes_no_webhook_row(self) -> None:
        """The event it synthesises is a message to `apply_psp_state`, not a
        record of one the PSP sent. Storing it would make the webhook log — the
        evidence in a dispute — a mixture of what the gateway said and what we
        asked it."""
        payment = _at_the_gateway()

        services.poll_payment(int(payment.pk))

        assert PaymentWebhookEvent.objects.count() == 0

    def test_a_capture_found_by_polling_is_applied_like_any_other(self) -> None:
        """A webhook that never arrives must cost latency and nothing else."""
        payment = _at_the_gateway()
        get_payment_port().capture(payment.psp_reference or "", idempotency_key=uuid.uuid4().hex)

        # `confirm_trip` is the booking module's, and this payment has no
        # basket behind it — its trip id belongs to no trip. The state change
        # is what this asserts; TC-070 walks the whole routine over HTTP.
        services.poll_payment(int(payment.pk))

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.CAPTURED
        assert payment.captured_at is not None
