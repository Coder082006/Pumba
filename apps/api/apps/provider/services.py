"""Application layer (SRS §8.2 layer 2).

    The ONLY module boundary. Other modules call this and nothing else
    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and
    emits domain events.

    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

Phase 7 gives this module its first use cases: an administrator creates a
provider, amends it, and moves it through §26.2's verification states, and the
booking engine asks whether a provider can be sold (BR-037).

**Nothing here resolves a region or audits a change.** `provider -> identity`
is the whole of this module's allowed graph (§6.4), so a region arrives as an id
and the audit entry is written by `administration`, which may see both the name
an administrator typed and the table it resolves to (ADR 0025).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import transaction

from apps.common.errors import NotFoundError, ValidationError
from apps.common.state_machine import IllegalTransitionError
from apps.provider import repositories as repo
from apps.provider.domain.verification import (
    VERIFY_MACHINE,
    ListingKind,
    ProviderType,
    VerifyState,
    is_sellable,
    may_own,
    path_to,
)
from apps.provider.dto import ProviderDTO, StatusChangeDTO
from apps.provider.models import Provider

__all__ = [
    "create_provider",
    "update_provider",
    "get_provider",
    "list_providers",
    "providers_by_id",
    "change_status",
    "require_owner_of",
    "WALKABLE_TARGETS",
    "SeedResult",
    "load_provider_seed",
]

#: The targets an administrator may reach in more than one step. Walking to
#: VERIFIED or REJECTED passes submission and review, which is the trail §26.2
#: describes. Suspension and reinstatement are single edges and are not walked:
#: "suspend this draft" is not a request anyone means.
WALKABLE_TARGETS = frozenset({VerifyState.VERIFIED, VerifyState.REJECTED})


def _dto(row: Provider) -> ProviderDTO:
    return ProviderDTO(
        id=row.id,
        public_id=row.public_id,
        legal_name=row.legal_name,
        trading_name=row.trading_name,
        provider_type=row.provider_type,
        contact_email=str(row.contact_email),
        contact_phone=row.contact_phone,
        region_id=row.region_id,
        verify_status=row.verify_status,
        verified_at=row.verified_at,
        payout_account_ref=row.payout_account_ref,
        payout_currency=row.payout_currency,
        rating_avg=row.rating_avg,
        rating_count=row.rating_count,
        is_sellable=is_sellable(VerifyState(row.verify_status)),
    )


def create_provider(**fields: Any) -> ProviderDTO:
    return _dto(repo.create(**fields))


def update_provider(public_id: UUID, **fields: Any) -> ProviderDTO:
    row = repo.find(public_id)
    if row is None:
        raise NotFoundError()
    return _dto(repo.update(row, **fields))


def get_provider(public_id: UUID) -> ProviderDTO:
    row = repo.find(public_id)
    if row is None:
        raise NotFoundError()
    return _dto(row)


def list_providers(
    *, provider_type: str | None = None, verify_status: str | None = None
) -> list[ProviderDTO]:
    return [
        _dto(row) for row in repo.listing(provider_type=provider_type, verify_status=verify_status)
    ]


def providers_by_id(ids: Sequence[int]) -> dict[int, ProviderDTO]:
    """Batch lookup for callers that store a provider as an id (ADR 0012)."""
    return {row.id: _dto(row) for row in repo.by_ids(ids)}


@transaction.atomic
def change_status(public_id: UUID, target: str, *, now: datetime) -> StatusChangeDTO:
    """Move a provider to `target` along §26.2's declared edges.

    Walks when the target is in `WALKABLE_TARGETS`, otherwise takes exactly one
    edge. Either way every hop is validated by `VERIFY_MACHINE`, so an illegal
    request fails before anything is written.

    `verified_at` is stamped on each arrival at VERIFIED, including a
    reinstatement — the column answers "since when may this provider be sold",
    and a reinstated provider's answer is the reinstatement.
    """
    try:
        wanted = VerifyState(target)
    except ValueError as exc:
        raise ValidationError(
            f"{target!r} is not a verification status.",
            details=[{"field": "status", "issue": "unknown"}],
        ) from exc

    row = repo.find_for_update(public_id)
    if row is None:
        raise NotFoundError()

    before = VerifyState(row.verify_status)
    if wanted in WALKABLE_TARGETS:
        steps = path_to(before, wanted)
    else:
        VERIFY_MACHINE.transition(before, wanted)
        steps = (wanted,)

    if not steps:
        raise IllegalTransitionError(VERIFY_MACHINE.name, before, wanted)

    final = steps[-1]
    row = repo.set_status(
        row,
        final.value,
        verified_at=now if VerifyState.VERIFIED in steps else None,
    )
    return StatusChangeDTO(
        before=before.value,
        steps=tuple(step.value for step in steps),
        provider=_dto(row),
    )


def require_owner_of(provider_public_id: UUID, kind: str) -> ProviderDTO:
    """The provider a listing of `kind` may be assigned to, or a 422.

    §7.5.3's "a provider may not own listings of a type inconsistent with
    provider_type", in the one place a listing is given an owner. Being
    unverified is *not* refused here: a listing may be prepared before its
    provider is approved, and BR-037 is checked when somebody tries to book it.
    """
    row = repo.find(provider_public_id)
    if row is None:
        raise ValidationError(
            "No such provider.", details=[{"field": "provider", "issue": "unknown"}]
        )
    if not may_own(ProviderType(row.provider_type), ListingKind(kind)):
        raise ValidationError(
            f"A {row.provider_type} provider cannot own a {kind} listing.",
            details=[{"field": "provider", "issue": "wrong_provider_type"}],
        )
    return _dto(row)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """What one file did — the same three fields as the other modules' loaders,
    and not the same class, because §6.4 gives `provider -> identity` only."""

    entity: str
    created: int
    updated: int

    def __str__(self) -> str:
        return f"{self.entity}: {self.created} created, {self.updated} updated"


def load_provider_seed(
    rows: Sequence[Mapping[str, Any]], *, now: datetime
) -> tuple[SeedResult, dict[str, ProviderDTO]]:
    """Seeded providers, identified by `legal_name`, walked to their status.

    §7.5.3 gives a provider no code or slug, and the registered legal name is
    the one thing two rows for the same business agree on. Idempotent: an
    existing live row is updated rather than duplicated.

    **The status is reached through the machine, not written.** A seed row
    saying VERIFIED is walked there by `change_status`, so a seeded provider
    has the same verified-at stamp and passes the same edges as one an
    administrator verified — seed data that could reach a state the console
    cannot would be seed data that tests something production never does.

    Returns the providers by legal name, so the caller can assign listings.
    """
    created = updated = 0
    by_name: dict[str, ProviderDTO] = {}
    for raw in rows:
        fields = dict(raw)
        status = fields.pop("verify_status", VerifyState.DRAFT.value)
        name = fields.get("legal_name")
        if not name:
            raise ValidationError("a provider seed row needs a legal_name")
        existing = Provider.objects.filter(legal_name=name).first()
        if existing is None:
            row = repo.create(**fields)
            created += 1
        else:
            fields.pop("provider_type", None)
            row = repo.update(existing, **fields)
            updated += 1
        if row.verify_status != status:
            change_status(row.public_id, status, now=now)
            row.refresh_from_db()
        by_name[name] = _dto(row)
    return SeedResult("provider", created, updated), by_name
