"""In-memory fake implementations of every port.

SRS principle A3 requires "a fake for tests" alongside each port. These are
the reference implementations used by unit and integration tests, by the
`fake` adapter setting in local development, and as the executable
documentation of what each protocol means.

**Determinism is the design constraint.** SRS principle A7 requires
deterministic behaviour, and TC-902 asserts that repeated identical requests
produce byte-identical results. So `FakeRouting` derives distances from the
coordinates by a fixed formula rather than returning random or canned values:
the same input always yields the same route, in every process, forever.
"""

from __future__ import annotations

import hashlib
import math
from itertools import count
from typing import Any

from apps.common.errors import ValidationError
from apps.common.money import Money
from ports.notification import DeliveryResult, DeliveryStatus
from ports.payment import (
    PaymentAction,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    RefundResult,
    WebhookEvent,
)
from ports.routing import Coordinate, MatrixResult, Place, RouteResult
from ports.storage import PresignedUpload, StoredObject

__all__ = [
    "FakeRouting",
    "FakePaymentGateway",
    "FakePush",
    "FakeEmail",
    "FakeSms",
    "FakeStorage",
]

_EARTH_RADIUS_M = 6_371_000
#: Straight-line distance inflated to approximate road distance. Zanzibar's
#: road network is not a grid; 1.35 is a plausible constant for a fake and is
#: never used for a real quote.
_ROAD_FACTOR = 1.35
#: 40 km/h average, expressed as seconds per metre.
_SECONDS_PER_METRE = 3600 / 40_000


def _haversine_metres(a: Coordinate, b: Coordinate) -> int:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return int(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h)))


class FakeRouting:
    """Deterministic routing. Same inputs, same outputs, always."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def route(self, origin: Coordinate, destination: Coordinate) -> RouteResult:
        self.calls.append(("route", (origin, destination)))
        straight = _haversine_metres(origin, destination)
        distance = int(straight * _ROAD_FACTOR)
        return RouteResult(
            distance_metres=distance,
            duration_seconds=int(distance * _SECONDS_PER_METRE),
            geometry=None,
        )

    def distance_matrix(
        self, origins: list[Coordinate], destinations: list[Coordinate]
    ) -> MatrixResult:
        self.calls.append(("distance_matrix", (origins, destinations)))
        return MatrixResult(
            cells=tuple(tuple(self.route(o, d) for d in destinations) for o in origins)
        )

    def geocode(self, query: str) -> list[Place]:
        self.calls.append(("geocode", query))
        if not query.strip():
            return []
        # Stable pseudo-coordinate derived from the query string.
        digest = hashlib.sha256(query.strip().lower().encode()).digest()
        lat = -6.0 - (digest[0] / 255)
        lng = 39.0 + (digest[1] / 255)
        return [
            Place(
                formatted_address=query.strip(),
                coordinate=Coordinate(lat=round(lat, 6), lng=round(lng, 6)),
                provider_place_id=f"fake_{digest.hex()[:12]}",
            )
        ]

    def reverse_geocode(self, coordinate: Coordinate) -> Place | None:
        self.calls.append(("reverse_geocode", coordinate))
        return Place(
            formatted_address=f"{coordinate.lat:.4f}, {coordinate.lng:.4f}",
            coordinate=coordinate,
            provider_place_id=None,
        )


class FakePaymentGateway:
    """In-memory PSP.

    Records every intent so tests can assert on idempotency, and enforces the
    refund ceiling of SRS §32.3 `REFUND_EXCEEDS_CAPTURED` so that rule is
    exercised without a real provider.
    """

    def __init__(self) -> None:
        self.intents: dict[str, PaymentIntent] = {}
        self._by_idempotency_key: dict[str, str] = {}
        self.refunds: dict[str, list[RefundResult]] = {}
        self._sequence = count(1)

    def _next_reference(self, prefix: str) -> str:
        return f"{prefix}_fake_{next(self._sequence):08d}"

    def create_intent(
        self,
        *,
        amount: Money,
        method: PaymentMethod,
        idempotency_key: str,
        return_url: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> PaymentIntent:
        # Replaying a key returns the original intent — never a second charge.
        if idempotency_key in self._by_idempotency_key:
            return self.intents[self._by_idempotency_key[idempotency_key]]

        reference = self._next_reference("pi")
        action = (
            PaymentAction("CLIENT_SECRET", {"client_secret": f"{reference}_secret"})
            if method is PaymentMethod.CARD
            else PaymentAction("USSD_PUSH", {"instruction": "Approve the prompt on your handset"})
        )
        intent = PaymentIntent(
            psp_reference=reference,
            status=PaymentIntentStatus.PENDING,
            amount=amount,
            action=action,
        )
        self.intents[reference] = intent
        self._by_idempotency_key[idempotency_key] = reference
        return intent

    def capture(self, psp_reference: str, *, idempotency_key: str) -> PaymentIntent:
        intent = self._require(psp_reference)
        captured = PaymentIntent(
            psp_reference=intent.psp_reference,
            status=PaymentIntentStatus.CAPTURED,
            amount=intent.amount,
            action=None,
        )
        self.intents[psp_reference] = captured
        return captured

    def refund(
        self, psp_reference: str, *, amount: Money, idempotency_key: str, reason: str = ""
    ) -> RefundResult:
        intent = self._require(psp_reference)
        already = sum(
            (r.amount.amount for r in self.refunds.get(psp_reference, [])),
            start=amount.amount * 0,
        )
        if already + amount.amount > intent.amount.amount:
            raise ValidationError(
                "Refund exceeds the remaining capturable amount.",
                code="REFUND_EXCEEDS_CAPTURED",
            )
        result = RefundResult(
            psp_refund_reference=self._next_reference("re"), amount=amount, settled=True
        )
        self.refunds.setdefault(psp_reference, []).append(result)
        return result

    def fetch_status(self, psp_reference: str) -> PaymentIntent:
        return self._require(psp_reference)

    def verify_webhook(self, *, payload: bytes, headers: dict[str, str]) -> WebhookEvent:
        if headers.get("X-Fake-Signature") != "valid":
            raise ValidationError("Webhook signature verification failed.")
        import json

        body = json.loads(payload)
        return WebhookEvent(
            event_id=body["event_id"],
            event_type=body["event_type"],
            psp_reference=body["psp_reference"],
            status=PaymentIntentStatus(body["status"]),
            raw=body,
        )

    def _require(self, psp_reference: str) -> PaymentIntent:
        if psp_reference not in self.intents:
            raise ValidationError(f"Unknown payment reference {psp_reference!r}.")
        return self.intents[psp_reference]


class _RecordingSender:
    """Shared behaviour for the three notification fakes."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.reject: set[str] = set()

    def _record(self, recipient: str, **fields: Any) -> DeliveryResult:
        self.sent.append({"recipient": recipient, **fields})
        if recipient in self.reject:
            return DeliveryResult(DeliveryStatus.REJECTED, error="recipient rejected")
        return DeliveryResult(
            DeliveryStatus.ACCEPTED, provider_message_id=f"fake_{len(self.sent):06d}"
        )


class FakePush(_RecordingSender):
    def send(
        self,
        *,
        device_token: str,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> DeliveryResult:
        return self._record(device_token, title=title, body=body, data=data or {})


class FakeEmail(_RecordingSender):
    def send(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        template_id: str | None = None,
        context: dict[str, str] | None = None,
    ) -> DeliveryResult:
        return self._record(to, subject=subject, html_body=html_body, template_id=template_id)


class FakeSms(_RecordingSender):
    def send(self, *, to_e164: str, body: str) -> DeliveryResult:
        return self._record(to_e164, body=body)


class FakeStorage:
    """In-memory object store."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def presign_upload(
        self,
        *,
        key: str,
        content_type: str,
        max_bytes: int,
        expires_in_seconds: int = 900,
    ) -> PresignedUpload:
        return PresignedUpload(
            url=f"https://fake-storage.local/{key}",
            fields={"Content-Type": content_type, "x-max-bytes": str(max_bytes)},
            key=key,
            expires_in_seconds=expires_in_seconds,
        )

    def presign_download(self, key: str, *, expires_in_seconds: int = 300) -> str:
        return f"https://fake-storage.local/{key}?expires={expires_in_seconds}"

    def put(self, *, key: str, data: bytes, content_type: str) -> StoredObject:
        self.objects[key] = (data, content_type)
        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=hashlib.md5(data, usedforsecurity=False).hexdigest(),
        )

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise ValidationError(f"No object at {key!r}.")
        return self.objects[key][0]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects
