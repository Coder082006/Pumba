"""The §7.2 `updated_at` trigger, exercised rather than assumed.

`TimestampedModel.save()` maintains the column for ordinary ORM writes. The
trigger exists for the writes that never reach `save()`: `QuerySet.update()`,
`bulk_update`, a data migration, a `COPY`, a hand-run correction. Those are
exactly the writes somebody is trying to reconstruct afterwards, which is when
a stale timestamp costs the most.

The clock matters as much as the trigger. `now()` is the transaction start
time, so under the single transaction a test runs in it is *earlier* than the
Python timestamp `created_at` was given moments before - the row appears to
have been modified before it existed. That is what these assertions pin.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.common.models import IdempotencyRecord

pytestmark = pytest.mark.django_db


def _record(key: str = "k-1") -> IdempotencyRecord:
    return IdempotencyRecord.objects.create(
        key=key,
        endpoint="/v1/example",
        principal_id=None,
        request_fingerprint="f" * 64,
        response_status=201,
        response_body={},
        expires_at=timezone.now() + dt.timedelta(hours=24),
    )


def test_a_bulk_update_moves_updated_at_forward() -> None:
    record = _record()
    before = record.updated_at

    IdempotencyRecord.objects.filter(pk=record.pk).update(response_status=200)

    record.refresh_from_db()
    assert record.updated_at > before


def test_updated_at_never_precedes_created_at() -> None:
    """The regression that `now()` produced.

    Inside one transaction `now()` is frozen at its start, so the trigger wrote
    a timestamp from before the insert. Nothing raised; the row simply carried
    an impossible history.
    """
    record = _record("k-2")

    IdempotencyRecord.objects.filter(pk=record.pk).update(response_status=200)

    record.refresh_from_db()
    assert record.updated_at >= record.created_at


def test_the_trigger_wins_over_a_supplied_value() -> None:
    """An explicit `updated_at` in an `update()` is not a way to backdate a
    row: the trigger overwrites it. Audit depends on that."""
    record = _record("k-3")
    stale = timezone.now() - dt.timedelta(days=30)

    IdempotencyRecord.objects.filter(pk=record.pk).update(updated_at=stale)

    record.refresh_from_db()
    assert record.updated_at > stale
