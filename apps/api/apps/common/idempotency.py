"""Idempotency-Key store — SRS §9.1, principle A6.

    "Idempotency-Key header required on all POST that create bookings,
     payments or assignments; server stores key -> response for 24 h."

**Why this lives in `common`.** The requirement spans `booking`, `payment` and
`transport`, and SRS §6.4 assigns the storage to none of them — the only
idempotency column in the schema is `payment.idempotency_key`, which covers
payments alone. Issue S2 in docs/IMPLEMENTATION-PLAN.md. It is infrastructure,
not a business module, so it sits in `common` where all three may reach it.

Phase 1 shipped the store. Phase 5 adds the mechanism and its first consumer:
§9.4.5's `POST /trips/{id}/quote`, which says *"Idempotency-Key: required"* and
is the endpoint the requirement matters most on — it takes row locks on shared
counters and hands back a time-boxed offer, so a retry that ran it twice would
hold a second set of seats and start a second clock.

**The key is scoped by (endpoint, principal) as well as by its value**, so one
client's key can never replay another's response, and the same key on a
different endpoint is a different operation. `endpoint` is the request *path*
rather than the route name, which matters for a route with a parameter in it:
`POST /trips/A/quote` and `POST /trips/B/quote` are different operations, and
scoping by the name alone would let one key reuse the other's response — a bug
the empty request body cannot catch, because the fingerprints match.

**Reserve, then fill.** The record is written before the handler runs, with no
response on it, and completed afterwards. The unique constraint is therefore
what serialises two simultaneous requests carrying one key: the second loses
the insert and is told the first is still in flight, rather than both executing
and one of them silently winning. A store that wrote only on the way out would
be a replay cache, not a lock, and the case it exists for — a client whose
request timed out and retried — is exactly the case where both are in flight.

**Only a success is remembered.** §9.1 says *"stores key -> response"* and does
not say which; replaying failures is the reading that does harm here. A
`409 INVENTORY_UNAVAILABLE` is a statement about the world at one instant, and
serving it again for twenty-four hours would tell a tourist a departure is full
long after the sweeper released the seats. So a failed attempt releases its
reservation and the same key may be tried again — which is what a client
retrying after an error wants, and costs nothing, because the operation did not
happen.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from functools import wraps
from typing import Any

from django.db import IntegrityError, models, transaction
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.response import Response

from apps.common.config import get_setting
from apps.common.errors import ConflictError, ValidationError
from apps.common.models import TimestampedModel

__all__ = [
    "IdempotencyRecord",
    "build_request_fingerprint",
    "IDEMPOTENCY_HEADER",
    "IdempotencyKeyRequiredError",
    "IdempotencyKeyReusedError",
    "RequestInProgressError",
    "idempotent",
]

#: §9.1's header. DRF normalises request headers, so the lookup below uses the
#: WSGI spelling; the constant is the one a client sends and a document names.
IDEMPOTENCY_HEADER = "Idempotency-Key"

#: The longest key the column holds. A client sending more has a bug worth
#: naming rather than a value worth truncating — a silently shortened key
#: collides with every other key sharing its first 64 characters.
MAX_KEY_LENGTH = 64


def build_request_fingerprint(body: Any) -> str:
    """Stable hash of a request body.

    Used to detect a client reusing one key for two *different* requests,
    which is a client bug and must surface as a 409 rather than silently
    replaying the wrong response.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyRecord(TimestampedModel):
    """One captured response for one (key, endpoint, principal) triple."""

    key = models.CharField(max_length=64)
    endpoint = models.CharField(max_length=200)
    # Nullable: some idempotent POSTs are unauthenticated (e.g. registration).
    principal_id = models.BigIntegerField(null=True, blank=True)

    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()

    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "idempotency_record"
        constraints = [
            models.UniqueConstraint(
                fields=["key", "endpoint", "principal_id"],
                name="uniq_idempotency_key_scope",
            )
        ]
        indexes = [models.Index(fields=["expires_at"], name="idx_idempotency_expiry")]

    def __str__(self) -> str:
        return f"IdempotencyRecord({self.endpoint}, {self.key[:8]}...)"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class IdempotencyKeyRequiredError(ValidationError):
    """§9.4.5: *"Idempotency-Key: required."*

    A 422 rather than a silent pass. An endpoint that accepted the header when
    offered and ignored its absence would give a client no way to discover that
    its retries were unprotected — which is the state this endpoint was in
    until now.
    """

    code = "IDEMPOTENCY_KEY_REQUIRED"
    default_message = "This operation requires an Idempotency-Key header."


class IdempotencyKeyReusedError(ConflictError):
    """One key, two different requests. A client bug, and §9.1 wants it said.

    Replaying the first response would be worse than an error: the client asked
    for something else and would be told it succeeded.
    """

    code = "IDEMPOTENCY_KEY_REUSED"
    default_message = (
        "This Idempotency-Key was already used for a different request. "
        "Use a new key for a new operation."
    )


class RequestInProgressError(ConflictError):
    """The first request carrying this key has not finished yet.

    Retryable, and says so: the honest answer to *"did my quote go through?"*
    while it is still going through is "ask again shortly", not a second quote.
    """

    code = "IDEMPOTENT_REQUEST_IN_PROGRESS"
    retryable = True
    default_message = "A request with this Idempotency-Key is still in progress. Retry shortly."


#: A reservation carries this until the handler returns. `PositiveSmallInteger`
#: cannot hold a sentinel outside its range and no HTTP status is zero, so it
#: is unambiguous without a nullable column that every read would have to
#: special-case.
_IN_FLIGHT = 0


def _retention() -> timedelta:
    return timedelta(hours=int(get_setting("idempotency.retention_hours")))


def _reserve(
    *, key: str, endpoint: str, principal_id: int | None, fingerprint: str
) -> IdempotencyRecord | None:
    """Claim the key, or return the record that already holds it.

    `None` means the claim succeeded and the caller owns the operation. A
    returned record means somebody got there first — in flight, finished, or
    finished with a different request; the caller decides which of the three
    answers applies.

    An expired record is deleted and the claim retried once. §9.1's twenty-four
    hours is a retention window, not a lifetime lease: a key from yesterday is
    a key that may be used again.
    """
    for _ in range(2):
        try:
            with transaction.atomic():
                IdempotencyRecord.objects.create(
                    key=key,
                    endpoint=endpoint,
                    principal_id=principal_id,
                    request_fingerprint=fingerprint,
                    response_status=_IN_FLIGHT,
                    response_body={},
                    expires_at=timezone.now() + _retention(),
                )
            return None
        except IntegrityError:
            existing = IdempotencyRecord.objects.filter(
                key=key, endpoint=endpoint, principal_id=principal_id
            ).first()
            if existing is None:
                # Deleted between the failed insert and this read. Claim again.
                continue
            if existing.is_expired:
                IdempotencyRecord.objects.filter(pk=existing.pk).delete()
                continue
            return existing
    return None


def idempotent(handler: Callable[..., Response]) -> Callable[..., Response]:
    """Make one DRF handler method idempotent per `Idempotency-Key` — §9.1, A6.

    Wraps `post`. The header is **required**: its absence is a 422 naming it,
    because an endpoint that silently tolerated the omission would leave a
    client believing it was protected.

    The decorated handler runs at most once per (key, path, principal). A
    second request carrying the same key gets the stored response, the same
    status, and no side effects — which on §9.4.5 means no second set of holds
    and no second twenty-minute clock.

    Deliberately a decorator on the handler rather than middleware. Middleware
    would have to guess which endpoints are idempotent from their method and
    path, and the guess would be wrong in the safe-looking direction — an
    endpoint added later would be unprotected and nothing would say so. Here it
    is one line on the view, visible in review, and absent means absent.
    """

    @wraps(handler)
    def wrapper(self: object, request: Request, *args: object, **kwargs: object) -> Response:
        key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
        if not key:
            raise IdempotencyKeyRequiredError(
                details=[{"field": IDEMPOTENCY_HEADER, "issue": "required"}]
            )
        if len(key) > MAX_KEY_LENGTH:
            raise ValidationError(
                f"{IDEMPOTENCY_HEADER} may be at most {MAX_KEY_LENGTH} characters.",
                details=[{"field": IDEMPOTENCY_HEADER, "issue": "too_long"}],
            )

        # The path, not the route name — see the module docstring.
        endpoint = request.path[:200]
        principal = getattr(request, "user", None)
        principal_id = getattr(principal, "id", None) if principal is not None else None
        fingerprint = build_request_fingerprint(request.data if request.body else None)

        existing = _reserve(
            key=key, endpoint=endpoint, principal_id=principal_id, fingerprint=fingerprint
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyKeyReusedError()
            if existing.response_status == _IN_FLIGHT:
                raise RequestInProgressError()
            return Response(existing.response_body, status=existing.response_status)

        try:
            response = handler(self, request, *args, **kwargs)
        except BaseException:
            # The operation did not happen, so the key did not either. Releasing
            # it is what lets a client retry after a 409 the world has since
            # resolved — a sold-out departure whose holds have expired.
            IdempotencyRecord.objects.filter(
                key=key, endpoint=endpoint, principal_id=principal_id
            ).delete()
            raise

        if 200 <= response.status_code < 300:
            # `.data` and not `.content`: the response has not been rendered
            # yet, and storing rendered bytes would freeze a content type the
            # replay might be asked to serve differently.
            IdempotencyRecord.objects.filter(
                key=key, endpoint=endpoint, principal_id=principal_id
            ).update(response_status=response.status_code, response_body=response.data)
        else:
            IdempotencyRecord.objects.filter(
                key=key, endpoint=endpoint, principal_id=principal_id
            ).delete()
        return response

    return wrapper
