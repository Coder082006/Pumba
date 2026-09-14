"""Data-access layer (SRS §8.2 layer 4). All ORM writes.

The writable set is stated here as well as in the admin serializer, for the
reason `apps.catalogue.repositories._WRITABLE` gives: the serializer names the
field an administrator got wrong, and this holds for a caller that never passed
through one — the seed loader, a shell, a future portal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from apps.common.errors import ValidationError
from apps.provider.models import Provider

__all__ = [
    "WRITABLE_ON_CREATE",
    "WRITABLE_ON_UPDATE",
    "create",
    "update",
    "find",
    "find_for_update",
    "by_ids",
    "listing",
    "set_status",
]

#: Everything an administrator may supply. `verify_status`, `verified_at` and
#: the ratings are absent: the first two move only through the machine, and the
#: ratings are a projection from reviews (§7.5.3 "denormalised").
WRITABLE_ON_CREATE = frozenset(
    {
        "legal_name",
        "trading_name",
        "provider_type",
        "contact_email",
        "contact_phone",
        "region_id",
        "payout_account_ref",
        "payout_currency",
    }
)

#: `provider_type` is fixed once the row exists. §7.5.3 forbids a provider
#: owning listings of another type, and a type that could change would turn
#: every listing it already owns into that violation at once.
WRITABLE_ON_UPDATE = WRITABLE_ON_CREATE - {"provider_type"}


def _reject(fields: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValidationError(
            "These provider fields cannot be written here.",
            details=[{"field": name, "issue": "not_writable"} for name in unknown],
        )


def create(**fields: Any) -> Provider:
    _reject(fields, WRITABLE_ON_CREATE)
    row = Provider(**fields)
    row.full_clean()
    row.save()
    return row


def update(row: Provider, **fields: Any) -> Provider:
    _reject(fields, WRITABLE_ON_UPDATE)
    for name, value in fields.items():
        setattr(row, name, value)
    row.full_clean()
    row.save(update_fields=[*fields, "updated_at"])
    return row


def find(public_id: UUID) -> Provider | None:
    return cast("Provider | None", Provider.objects.filter(public_id=public_id).first())


def find_for_update(public_id: UUID) -> Provider | None:
    """Locked, so two administrators moving one provider serialise.

    Without it, one "verify" and one "reject" can each read UNDER_REVIEW, each
    pass the machine, and the second write silently wins over a transition the
    first had already audited.
    """
    return cast(
        "Provider | None",
        Provider.objects.select_for_update().filter(public_id=public_id).first(),
    )


def by_ids(ids: Iterable[int]) -> list[Provider]:
    return list(Provider.objects.filter(id__in=set(ids)).order_by("id"))


def listing(*, provider_type: str | None, verify_status: str | None) -> list[Provider]:
    rows = Provider.objects.all()
    if provider_type:
        rows = rows.filter(provider_type=provider_type)
    if verify_status:
        rows = rows.filter(verify_status=verify_status)
    return list(rows.order_by("trading_name", "id"))


def set_status(row: Provider, status: str, *, verified_at: datetime | None) -> Provider:
    """Writes an already-validated status.

    Deliberately does not consult the machine: `services` has, and a repository
    that checked again would be the second place the rule lives.
    """
    row.verify_status = status
    fields = ["verify_status", "updated_at"]
    if verified_at is not None:
        row.verified_at = verified_at
        fields.append("verified_at")
    row.save(update_fields=fields)
    return row
