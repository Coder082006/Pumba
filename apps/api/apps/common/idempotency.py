"""Idempotency-Key store — SRS §9.1, principle A6.

    "Idempotency-Key header required on all POST that create bookings,
     payments or assignments; server stores key -> response for 24 h."

**Why this lives in `common`.** The requirement spans `booking`, `payment` and
`transport`, and SRS §6.4 assigns the storage to none of them — the only
idempotency column in the schema is `payment.idempotency_key`, which covers
payments alone. Issue S2 in docs/IMPLEMENTATION-PLAN.md. It is infrastructure,
not a business module, so it sits in `common` where all three may reach it.

Phase 1 ships the store and the replay mechanism. No mutating endpoint exists
yet to use it; the first consumer is Phase 7's booking creation.

The key is scoped by (endpoint, principal) as well as the client-supplied
value, so one client's key can never replay another's response, and the same
key on a different endpoint is a different operation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import models
from django.utils import timezone

from apps.common.models import TimestampedModel

__all__ = ["IdempotencyRecord", "build_request_fingerprint"]


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
