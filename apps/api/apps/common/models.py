"""Base model implementing the mandatory conventions of SRS §7.2.

    Primary keys   BIGSERIAL internally; `public_id` UUID exposed externally.
                   "Sequential integers are never returned to clients."
    Timestamps     created_at / updated_at TIMESTAMPTZ NOT NULL on every table.
    Soft deletion  deleted_at TIMESTAMPTZ NULL on catalogue and user-facing
                   entities. Financial and booking records are never
                   soft-deleted or hard-deleted.
    Concurrency    `version` for optimistic locking, on the specific tables
                   SRS §7.2 names.

`updated_at` is maintained here in `save()` rather than by the database
trigger SRS §7.2 mentions. A trigger is the more robust choice because it
also fires for raw SQL and bulk updates; adding it is a migration task for
Phase 2, when the first real tables land. Recorded so it is not forgotten.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, Generic, TypeVar

from django.db import models
from django.utils import timezone

__all__ = [
    "BaseModel",
    "SoftDeleteModel",
    "VersionedModel",
    "TimestampedModel",
    "IdempotencyRecord",
]


class TimestampedModel(models.Model):
    """created_at / updated_at on every table (SRS §7.2)."""

    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.updated_at = timezone.now()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "updated_at" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "updated_at"]
        super().save(*args, **kwargs)


class BaseModel(TimestampedModel):
    """Every externally exposed entity.

    APIs expose `public_id` only (SRS §7.2, §9.1, principle A6).
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.public_id})"


_SD = TypeVar("_SD", bound="SoftDeleteModel")


class SoftDeleteQuerySet(models.QuerySet[_SD], Generic[_SD]):
    def alive(self) -> SoftDeleteQuerySet[_SD]:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet[_SD]:
        return self.filter(deleted_at__isnull=False)

    def delete(self) -> tuple[int, dict[str, int]]:
        count = self.update(deleted_at=timezone.now())
        return count, {}


class SoftDeleteManager(models.Manager[_SD], Generic[_SD]):
    """Default manager excluding soft-deleted rows (SRS §7.2).

    `all_objects` is provided alongside for administrative and audit access.
    """

    def get_queryset(self) -> SoftDeleteQuerySet[_SD]:
        qs: SoftDeleteQuerySet[_SD] = SoftDeleteQuerySet(self.model, using=self._db)
        return qs.filter(deleted_at__isnull=True)


class SoftDeleteModel(BaseModel):
    """Catalogue and user-facing entities.

    Deliberately *not* inherited by financial or booking records — SRS §7.2:
    "Financial and booking records are never soft-deleted or hard-deleted."
    """

    deleted_at = models.DateTimeField(null=True, blank=True, default=None, editable=False)

    objects: ClassVar[SoftDeleteManager[Any]] = SoftDeleteManager()
    all_objects: ClassVar[models.Manager[SoftDeleteModel]] = models.Manager()

    class Meta:
        abstract = True

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])
        return 1, {type(self).__name__: 1}

    def hard_delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        return super().delete(*args, **kwargs)

    def restore(self) -> None:
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class VersionedModel(models.Model):
    """Optimistic locking.

    SRS §7.2 applies this to booking, inventory_hold, room_availability,
    activity_departure and driver_assignment. Mixed in by those models when
    they are built; the failure mode is SRS §32.3 `VERSION_CONFLICT` (409).
    """

    version = models.IntegerField(default=0, editable=False)

    class Meta:
        abstract = True


# `IdempotencyRecord` is defined in `idempotency.py`, next to the fingerprint
# helper it belongs with. Django discovers models by importing `<app>.models`
# and nothing else, so without this re-export the model is invisible to
# `makemigrations` and never gets a table — while still importing fine in a
# test, which is what makes the failure mode quiet.
#
# The import sits at the end of the file because `idempotency` imports
# `TimestampedModel` from here.
from apps.common.idempotency import IdempotencyRecord  # noqa: E402
