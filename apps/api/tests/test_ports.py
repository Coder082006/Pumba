"""Port protocol and fake tests — SRS principle A3, A7.

Two things matter here.

**The fakes must satisfy the protocols.** A fake that has drifted from its
port gives every test using it a false pass. `runtime_checkable` makes that
assertion cheap, so there is no excuse for not making it.

**The fakes must be deterministic.** SRS principle A7 requires deterministic
behaviour and TC-902 asserts repeated identical requests are byte-identical.
A fake that returned random or wall-clock-dependent values would let
non-determinism into the system through the test suite.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.common.errors import ValidationError
from apps.common.money import Money
from ports.fakes import (
    FakeEmail,
    FakePaymentGateway,
    FakePush,
    FakeRouting,
    FakeSms,
    FakeStorage,
)
from ports.notification import DeliveryStatus, EmailPort, PushPort, SmsPort
from ports.payment import PaymentGatewayPort, PaymentIntentStatus, PaymentMethod
from ports.routing import Coordinate, RoutingPort
from ports.storage import StoragePort

# Representative inputs only — no behaviour depends on the destination
# (SRS §4.2, principle A8).
ZNZ = Coordinate(lat=-6.2220, lng=39.2249)
NUNGWI = Coordinate(lat=-5.7263, lng=39.2967)


class TestFakesSatisfyTheirProtocols:
    @pytest.mark.parametrize(
        ("fake", "protocol"),
        [
            (FakeRouting(), RoutingPort),
            (FakePaymentGateway(), PaymentGatewayPort),
            (FakePush(), PushPort),
            (FakeEmail(), EmailPort),
            (FakeSms(), SmsPort),
            (FakeStorage(), StoragePort),
        ],
    )
    def test_conforms(self, fake: object, protocol: type) -> None:
        assert isinstance(fake, protocol), (
            f"{type(fake).__name__} has drifted from {protocol.__name__}; "
            "every test using it is passing for the wrong reason"
        )


class TestCoordinateValidation:
    @pytest.mark.parametrize(("lat", "lng"), [(91, 0), (-91, 0), (0, 181), (0, -181)])
    def test_rejects_out_of_range(self, lat: float, lng: float) -> None:
        with pytest.raises(ValueError):
            Coordinate(lat=lat, lng=lng)

    def test_accepts_the_extremes(self) -> None:
        Coordinate(lat=90, lng=180)
        Coordinate(lat=-90, lng=-180)


class TestFakeRoutingIsDeterministic:
    def test_same_input_gives_identical_output(self) -> None:
        """SRS principle A7 / TC-902."""
        assert FakeRouting().route(ZNZ, NUNGWI) == FakeRouting().route(ZNZ, NUNGWI)

    def test_distance_is_plausible_for_the_input(self) -> None:
        result = FakeRouting().route(ZNZ, NUNGWI)
        assert 40_000 < result.distance_metres < 90_000
        assert result.duration_seconds > 0

    def test_zero_distance_for_identical_points(self) -> None:
        assert FakeRouting().route(ZNZ, ZNZ).distance_metres == 0

    def test_distance_is_symmetric(self) -> None:
        routing = FakeRouting()
        assert routing.route(ZNZ, NUNGWI) == routing.route(NUNGWI, ZNZ)

    def test_matrix_is_row_major_origins_by_destinations(self) -> None:
        matrix = FakeRouting().distance_matrix([ZNZ, NUNGWI], [NUNGWI])
        assert len(matrix.cells) == 2
        assert len(matrix.cells[0]) == 1
        assert matrix.cells[0][0] == FakeRouting().route(ZNZ, NUNGWI)

    def test_geocode_is_stable_across_instances(self) -> None:
        assert FakeRouting().geocode("Stone Town") == FakeRouting().geocode("Stone Town")

    def test_geocode_resolves_case_and_space_to_the_same_coordinate(self) -> None:
        """The resolved location is normalised; the echoed address is not.

        Returning the caller's own spelling is the right behaviour for a
        geocoder, so only the coordinate is asserted to be stable.
        """
        loose = FakeRouting().geocode("  stone town ")
        exact = FakeRouting().geocode("Stone Town")
        assert loose[0].coordinate == exact[0].coordinate
        assert loose[0].formatted_address == "stone town"

    def test_geocode_of_blank_returns_nothing(self) -> None:
        assert FakeRouting().geocode("   ") == []

    def test_reverse_geocode_round_trips_the_coordinate(self) -> None:
        place = FakeRouting().reverse_geocode(ZNZ)
        assert place is not None
        assert place.coordinate == ZNZ


class TestFakePaymentGateway:
    def test_replaying_an_idempotency_key_returns_the_original_intent(self) -> None:
        """SRS principle A6: the same key must never produce a second charge."""
        gateway = FakePaymentGateway()
        amount = Money(Decimal("834.75"), "USD")

        first = gateway.create_intent(
            amount=amount, method=PaymentMethod.CARD, idempotency_key="k1"
        )
        second = gateway.create_intent(
            amount=amount, method=PaymentMethod.CARD, idempotency_key="k1"
        )

        assert first.psp_reference == second.psp_reference
        assert len(gateway.intents) == 1

    def test_distinct_keys_create_distinct_intents(self) -> None:
        gateway = FakePaymentGateway()
        amount = Money(Decimal("10.00"), "USD")
        a = gateway.create_intent(amount=amount, method=PaymentMethod.CARD, idempotency_key="k1")
        b = gateway.create_intent(amount=amount, method=PaymentMethod.CARD, idempotency_key="k2")
        assert a.psp_reference != b.psp_reference

    def test_card_and_mobile_money_return_different_actions(self) -> None:
        """SRS §9.4.7 returns a method-specific action object."""
        gateway = FakePaymentGateway()
        amount = Money(Decimal("10.00"), "USD")

        card = gateway.create_intent(amount=amount, method=PaymentMethod.CARD, idempotency_key="c")
        momo = gateway.create_intent(
            amount=amount, method=PaymentMethod.MOBILE_MONEY, idempotency_key="m"
        )

        assert card.action is not None and card.action.type == "CLIENT_SECRET"
        assert momo.action is not None and momo.action.type == "USSD_PUSH"

    def test_capture_moves_the_intent_to_captured(self) -> None:
        gateway = FakePaymentGateway()
        intent = gateway.create_intent(
            amount=Money(Decimal("50.00"), "USD"),
            method=PaymentMethod.CARD,
            idempotency_key="k",
        )
        captured = gateway.capture(intent.psp_reference, idempotency_key="cap")
        assert captured.status is PaymentIntentStatus.CAPTURED
        assert gateway.fetch_status(intent.psp_reference).status is PaymentIntentStatus.CAPTURED

    def test_refund_beyond_the_captured_amount_is_refused(self) -> None:
        """SRS §32.3 REFUND_EXCEEDS_CAPTURED."""
        gateway = FakePaymentGateway()
        intent = gateway.create_intent(
            amount=Money(Decimal("100.00"), "USD"),
            method=PaymentMethod.CARD,
            idempotency_key="k",
        )
        gateway.refund(
            intent.psp_reference, amount=Money(Decimal("60.00"), "USD"), idempotency_key="r1"
        )

        with pytest.raises(ValidationError) as exc:
            gateway.refund(
                intent.psp_reference,
                amount=Money(Decimal("50.00"), "USD"),
                idempotency_key="r2",
            )
        assert exc.value.code == "REFUND_EXCEEDS_CAPTURED"

    def test_partial_refunds_accumulate_up_to_the_captured_amount(self) -> None:
        gateway = FakePaymentGateway()
        intent = gateway.create_intent(
            amount=Money(Decimal("100.00"), "USD"),
            method=PaymentMethod.CARD,
            idempotency_key="k",
        )
        for index, part in enumerate(("40.00", "60.00")):
            gateway.refund(
                intent.psp_reference,
                amount=Money(Decimal(part), "USD"),
                idempotency_key=f"r{index}",
            )
        assert len(gateway.refunds[intent.psp_reference]) == 2

    def test_unknown_reference_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            FakePaymentGateway().fetch_status("nope")

    def test_webhook_without_a_valid_signature_is_refused(self) -> None:
        """SRS §9.4.8: an unverified payload must never reach the state machine."""
        with pytest.raises(ValidationError, match="signature"):
            FakePaymentGateway().verify_webhook(payload=b"{}", headers={})

    def test_verified_webhook_is_parsed(self) -> None:
        body = {
            "event_id": "evt_1",
            "event_type": "payment.captured",
            "psp_reference": "pi_1",
            "status": "CAPTURED",
        }
        event = FakePaymentGateway().verify_webhook(
            payload=json.dumps(body).encode(), headers={"X-Fake-Signature": "valid"}
        )
        assert event.event_id == "evt_1"
        assert event.status is PaymentIntentStatus.CAPTURED


class TestNotificationFakes:
    def test_each_channel_records_what_it_sent(self) -> None:
        push, email, sms = FakePush(), FakeEmail(), FakeSms()
        push.send(device_token="tok", title="t", body="b")
        email.send(to="a@example.com", subject="s", html_body="<p>x</p>")
        sms.send(to_e164="+255700000000", body="b")

        assert push.sent[0]["recipient"] == "tok"
        assert email.sent[0]["subject"] == "s"
        assert sms.sent[0]["body"] == "b"

    def test_a_rejected_recipient_returns_a_result_rather_than_raising(self) -> None:
        """SRS §19.4 needs per-recipient outcomes.

        Raising would lose the outcome for every other recipient in a batch.
        """
        sms = FakeSms()
        sms.reject.add("+255700000001")

        ok = sms.send(to_e164="+255700000000", body="b")
        bad = sms.send(to_e164="+255700000001", body="b")

        assert ok.ok and ok.status is DeliveryStatus.ACCEPTED
        assert not bad.ok and bad.status is DeliveryStatus.REJECTED


class TestFakeStorage:
    def test_put_get_exists_delete(self) -> None:
        storage = FakeStorage()
        stored = storage.put(key="k", data=b"hello", content_type="text/plain")

        assert stored.size_bytes == 5
        assert storage.exists("k")
        assert storage.get("k") == b"hello"

        storage.delete("k")
        assert not storage.exists("k")

    def test_get_of_a_missing_object_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            FakeStorage().get("missing")

    def test_delete_is_idempotent(self) -> None:
        FakeStorage().delete("never-existed")

    def test_presigned_upload_carries_its_constraints(self) -> None:
        upload = FakeStorage().presign_upload(
            key="docs/licence.pdf", content_type="application/pdf", max_bytes=5_000_000
        )
        assert upload.key == "docs/licence.pdf"
        assert upload.fields["x-max-bytes"] == "5000000"
        assert upload.expires_in_seconds == 900

    def test_presigned_download_is_time_boxed(self) -> None:
        """SRS §35.7: private objects are reached only through signed URLs."""
        url = FakeStorage().presign_download("docs/licence.pdf", expires_in_seconds=120)
        assert "expires=120" in url
