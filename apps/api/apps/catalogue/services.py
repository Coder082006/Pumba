"""catalogue module — SRS §6.4.

Application layer (SRS §8.2 layer 2).

    The ONLY module boundary. Other modules call this and nothing else
    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and
    emits domain events.

    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

    Public interface: search_activities(), get_destination(), list_accommodation()

This half of the module is the §27.8 console: what an administrator does to the
catalogue, and the audit trail §41.13 requires of it. The public read interface
named above arrives with the read API.

**One audited path, seven entities.** `ENTITIES` describes each curated table
once — its wire name, its model, its §5.2 resource and its two repository
functions — and `create`, `update`, `delete` and `restore` are written once
over that description. The alternative is twenty-eight functions that differ in
one identifier each, and the interesting property — that *every* one of them
audits — would then be twenty-eight separate things to get right rather than
one.

**The audit entry is written inside the transaction that made the change.**
§41.13 asks for an entry per administrative action; an entry written after the
commit can be lost by a crash in between, and one written before it can survive
a rollback and describe a change that never happened. `record_audit` cannot
fail the request either way — its sink swallows and logs — so the only thing
the transaction decides is whether the row and its audit entry land together.

**Before and after are the writable state, not the whole row.** §41.13 wants
what the administrator changed. `repositories.snapshot` derives that from the
same `_WRITABLE` set the write path enforces, so the two cannot drift, and
`created_at`, `search_vector` and the denormalised rating projections stay out
of a diff nobody edited.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

from django.db import transaction

from apps.catalogue import repositories as repo
from apps.catalogue.models import (
    Accommodation,
    Activity,
    Attraction,
    CancellationPolicy,
    Country,
    Destination,
    Region,
    Tag,
)
from apps.common.audit import AuditAction, record_audit
from apps.common.authz import Permission, Principal, Resource, Role
from apps.common.errors import NotFoundError, ValidationError
from apps.common.models import SoftDeleteModel

__all__ = [
    "CatalogueEntity",
    "ENTITIES",
    "entity_for",
    "resolve_references",
    "create",
    "update",
    "delete",
    "restore",
]


@dataclass(frozen=True, slots=True)
class CatalogueEntity:
    """One curated table, described once.

    `resource` is the §5.2 cell the view scopes by, and it lives here rather
    than on the view so that a new entity cannot arrive with a write path and
    no ownership rule — the two are declared in the same object.
    """

    key: str
    """The wire name and the audit `entity_type`. Matches the table name."""

    model: type[SoftDeleteModel]
    resource: Resource
    create: Callable[..., Any]
    update: Callable[..., Any]
    references: Mapping[str, type[SoftDeleteModel]] = field(default_factory=dict)
    """Fields that arrive as another row's `public_id` and must become a row."""


ENTITIES: Mapping[str, CatalogueEntity] = MappingProxyType(
    {
        entity.key: entity
        for entity in (
            CatalogueEntity(
                "country", Country, Resource.COUNTRY, repo.create_country, repo.update_country
            ),
            CatalogueEntity(
                "region",
                Region,
                Resource.REGION,
                repo.create_region,
                repo.update_region,
                {"country": Country},
            ),
            CatalogueEntity(
                "destination",
                Destination,
                Resource.DESTINATION,
                repo.create_destination,
                repo.update_destination,
                {"region": Region},
            ),
            CatalogueEntity("tag", Tag, Resource.TAG, repo.create_tag, repo.update_tag),
            CatalogueEntity(
                "attraction",
                Attraction,
                Resource.ATTRACTION,
                repo.create_attraction,
                repo.update_attraction,
                {"destination": Destination},
            ),
            CatalogueEntity(
                "activity",
                Activity,
                Resource.ACTIVITY,
                repo.create_activity,
                repo.update_activity,
                {
                    "destination": Destination,
                    "attraction": Attraction,
                    "cancellation_policy": CancellationPolicy,
                },
            ),
            CatalogueEntity(
                "accommodation",
                Accommodation,
                Resource.ACCOMMODATION,
                repo.create_accommodation,
                repo.update_accommodation,
                {"destination": Destination},
            ),
        )
    }
)


def entity_for(key: str) -> CatalogueEntity:
    return ENTITIES[key]


def resolve_references(entity: CatalogueEntity, fields: Mapping[str, Any]) -> dict[str, Any]:
    """Swap each `public_id` in `fields` for the row it names.

    A dangling reference is a 422 naming the field, not a 404: the request is
    well-formed and points at nothing, and the thing that was not found is a
    value inside the body rather than the resource being addressed. Answering
    404 here would make "the destination you named does not exist"
    indistinguishable from "the attraction you are editing does not exist".

    A null is passed through untouched — `attraction` and `cancellation_policy`
    are both optional on an activity, and clearing one is a legitimate edit.
    """
    resolved = dict(fields)
    for name, model in entity.references.items():
        public_id = resolved.get(name)
        if public_id is None:
            continue
        row = repo.reference(model, public_id)
        if row is None:
            raise ValidationError(
                f"No {name} exists with that identifier.",
                details=[{"field": name, "issue": "No such row."}],
            )
        resolved[name] = row
    return resolved


#: Which role authorised a catalogue write, for the §41.13 `actor_role`
#: column. Ordered most-specific first: a SUPER_ADMIN who also holds
#: CATALOGUE_ADMIN was acting as the latter, and that is the more useful thing
#: for an investigator to read.
_CATALOGUE_ROLES = (Role.CATALOGUE_ADMIN, Role.SUPER_ADMIN)


def _authorising_role(principal: Principal | None) -> str | None:
    """The role that permitted this action, not the principal's whole role set.

    `audit_log.actor_role` is one column and §41.13 asks for "role", singular.
    A joined list would overflow it and would answer a question nobody asked:
    what matters is which grant was exercised.
    """
    if principal is None:
        return None
    for role in _CATALOGUE_ROLES:
        if principal.has_role(role):
            return str(role)
    # Reached only if some future role gains CATALOGUE_MANAGE without being
    # listed above. Recording the permission is less useful than recording the
    # role, and much better than recording nothing.
    return str(Permission.CATALOGUE_MANAGE) if principal.has(Permission.CATALOGUE_MANAGE) else None


def _audit(
    action: AuditAction,
    entity: CatalogueEntity,
    public_id: UUID,
    *,
    principal: Principal | None,
    ip: str | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    record_audit(
        action,
        entity_type=entity.key,
        entity_id=str(public_id),
        actor_user_id=principal.user_id if principal is not None else None,
        actor_role=_authorising_role(principal),
        before=before,
        after=after,
        ip=ip,
    )


def _require(entity: CatalogueEntity, public_id: UUID) -> dict[str, Any]:
    """The `before` state, or 404.

    A missing row is `NotFoundError` and never a 403, for the §30.3 reason: a
    distinguishable "exists but you may not touch it" confirms the row exists.
    The view has already restricted the queryset, so a principal who reaches
    this call may act on every row it can see.
    """
    before = repo.snapshot(entity.model, public_id)
    if before is None:
        raise NotFoundError()
    return before


@transaction.atomic
def create(
    entity: CatalogueEntity,
    *,
    fields: Mapping[str, Any],
    principal: Principal | None = None,
    ip: str | None = None,
) -> Any:
    """Create one curated row and record it.

    `before` is deliberately `{}` rather than absent: an empty dict beside a
    populated one reads as a creation at a glance, where a missing key reads as
    a bug in whatever wrote the entry.
    """
    dto = entity.create(**resolve_references(entity, fields))
    _audit(
        AuditAction.CATALOGUE_CREATED,
        entity,
        dto.public_id,
        principal=principal,
        ip=ip,
        before={},
        after=repo.snapshot(entity.model, dto.public_id),
    )
    return dto


@transaction.atomic
def update(
    entity: CatalogueEntity,
    public_id: UUID,
    *,
    fields: Mapping[str, Any],
    principal: Principal | None = None,
    ip: str | None = None,
) -> Any:
    """Apply a partial update and record both sides of it.

    This is also how a market opens and closes. §4.1 wants Arusha published and
    Pemba withdrawn without a deployment, and both are `is_active` moving in a
    PATCH — so both leave an entry whose diff says exactly which flag turned
    and who turned it.
    """
    before = _require(entity, public_id)
    dto = entity.update(public_id, **resolve_references(entity, fields))
    _audit(
        AuditAction.CATALOGUE_UPDATED,
        entity,
        public_id,
        principal=principal,
        ip=ip,
        before=before,
        after=repo.snapshot(entity.model, public_id),
    )
    return dto


@transaction.atomic
def delete(
    entity: CatalogueEntity,
    public_id: UUID,
    *,
    principal: Principal | None = None,
    ip: str | None = None,
) -> None:
    """§7.7's soft deletion. The row stays; the slug is released.

    `after` is the same writable state, because `deleted_at` is not writable
    and so does not appear in either snapshot. The action name carries what
    happened; the snapshots carry what the row was, which is what somebody
    restoring it needs.
    """
    before = _require(entity, public_id)
    repo.soft_delete(entity.model, public_id)
    _audit(
        AuditAction.CATALOGUE_DELETED,
        entity,
        public_id,
        principal=principal,
        ip=ip,
        before=before,
        after={},
    )


@transaction.atomic
def restore(
    entity: CatalogueEntity,
    public_id: UUID,
    *,
    principal: Principal | None = None,
    ip: str | None = None,
) -> None:
    """Undo a soft deletion.

    Fails on the partial unique index if the slug was reused while the row was
    gone, and that failure is correct rather than unfortunate — see
    `repositories.restore`. The audit entry is inside the transaction, so a
    failed restore leaves no entry claiming one happened.
    """
    before = _require(entity, public_id)
    repo.restore(entity.model, public_id)
    _audit(
        AuditAction.CATALOGUE_RESTORED,
        entity,
        public_id,
        principal=principal,
        ip=ip,
        before=before,
        after=repo.snapshot(entity.model, public_id),
    )
