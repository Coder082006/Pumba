"""administration module — SRS §6.4.

Owns:
        audit_log, system_setting, feature_flag, support_ticket

Interface:  record_audit(), get_setting()
Depends on: all (read via interfaces)
Layer:      L7

Application layer (SRS §8.2 layer 2).

The ONLY module boundary. Other modules call this and nothing else
(SRS §6.5 rule 1). Orchestrates a use case in one transaction and
emits domain events.

Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

Phase 2 implemented the audit sink. Phase 6 adds §27.11's tariff console, which
is here for the reason §6.4 gives this module "all": a corridor row names two
`catalogue` destinations and lives in a `transport` table, and no other module
may see both (ADR 0023).

**The division of labour is one sentence.** This module turns the names an
administrator typed into ids, because it is allowed to look them up;
`transport.services` does everything else, because it owns the tables. Nothing
here touches `transport.models` or `transport.repositories` — `private-transport`
forbids it, and the forbidding is the point: the moment this file could reach a
row directly, the ladder's inputs would start being assembled in two places.

**Every write is audited inside the transaction that made it.** §41.13 wants an
entry per administrative action; one written after the commit can be lost by a
crash in between, and one written before it can survive a rollback and describe
a change that never happened. The same reasoning `catalogue.services` gives, and
the same `record_audit` sink below, which swallows and logs rather than failing
the request.

**The preview runs the tourist's own code path.** §27.11 asks for "a quote-
preview tool"; a second implementation would eventually disagree with the first,
and the disagreement would surface as an administrator insisting a price is
right while a tourist is charged something else.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.administration.models import AuditLog
from apps.catalogue import services as catalogue
from apps.common.audit import AuditAction, AuditRecord, record_audit
from apps.common.authz import Principal
from apps.common.errors import NotFoundError, ValidationError
from apps.provider import services as provider
from apps.provider.dto import ProviderDTO
from apps.transport import services as transport
from apps.transport.dto import LegEndpoint, LegRequest

__all__ = [
    "write_audit_record",
    "create_corridor",
    "update_corridor",
    "create_tariff",
    "update_tariff",
    "preview_quote",
    "create_provider",
    "update_provider",
    "list_providers",
    "get_provider",
    "change_provider_status",
    "assign_activity_provider",
]


# -- the Phase 2 audit sink -------------------------------------------------


def write_audit_record(record: AuditRecord) -> None:
    """The sink registered on `apps.common.audit` at startup.

    Takes the port's value object rather than keyword arguments so that a new
    field on `AuditRecord` cannot be silently dropped in transit — it either
    lands in a column here or fails the type check.
    """
    AuditLog.objects.create(
        occurred_at=record.occurred_at,
        action=str(record.action),
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        actor_user_id=record.actor_user_id,
        actor_role=record.actor_role or "",
        before=record.before,
        after=record.after,
        ip=record.ip,
        request_id=record.request_id or "",
        reason=record.reason,
    )


# -- resolving what an administrator typed ----------------------------------


def _destination_id(slug: str, *, field: str = "destination") -> int:
    """A destination by the name a person writes.

    `resolve_planning_ref` rather than `resolve_listing_ref`, because the
    latter refuses a destination on purpose — a destination carries the
    currency and timezone a trip cannot be opened without, and offering a
    second door to that table would let a caller take the one that answers
    less.
    """
    ref = catalogue.resolve_planning_ref(slug, today=timezone.localdate())
    if ref is None:
        raise ValidationError(
            f"{slug!r} is not a live destination.",
            details=[{"field": field, "issue": "unknown"}],
        )
    return ref.storage_id


def _scope_id(kind: str, key: str) -> int:
    found = catalogue.resolve_scope_ref(kind, key)
    if found is None:
        raise ValidationError(
            f"{key!r} is not a {kind} the catalogue has a row for.",
            details=[{"field": kind, "issue": "unknown"}],
        )
    return found


def _corridor_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields = dict(payload)
    for wire, column in (
        ("origin_destination", "origin_destination_id"),
        ("target_destination", "target_destination_id"),
    ):
        if wire in fields:
            fields[column] = _destination_id(fields.pop(wire), field=wire)
    return fields


def _tariff_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields = dict(payload)
    for kind in ("region", "country"):
        if kind in fields:
            key = fields.pop(kind)
            fields[f"{kind}_id"] = None if key is None else _scope_id(kind, key)
    return fields


# -- rendering back ---------------------------------------------------------


def _named(dto: transport.CorridorAdminDTO) -> dict[str, Any]:
    """A corridor with its destinations named rather than numbered (§7.2).

    `transport` publishes the ids because ADR 0012 makes a cross-module
    reference an id; this is where they stop, in the module allowed to resolve
    them.
    """
    places = catalogue.transfer_places(
        "destination", [dto.origin_destination_id, dto.target_destination_id]
    )

    def slug(destination_id: int) -> str:
        place = places.get(destination_id)
        # A corridor pointing at a destination that no longer exists. No SQL
        # foreign key stops that (ADR 0012), so it is a real state, and an
        # empty name is more useful to an administrator than a crash.
        return "" if place is None else place.destination_slug

    return {
        "public_id": dto.public_id,
        "origin_destination": slug(dto.origin_destination_id),
        "target_destination": slug(dto.target_destination_id),
        "vehicle_class": dto.vehicle_class,
        "fixed_price": dto.fixed_price,
        "currency": dto.currency,
        "is_bidirectional": dto.is_bidirectional,
        "valid_from": dto.valid_from,
        "valid_to": dto.valid_to,
        "is_active": dto.is_active,
    }


def _tariff_payload(dto: transport.TariffAdminDTO) -> dict[str, Any]:
    return {
        "public_id": dto.public_id,
        "scope": dto.scope,
        "region": None if dto.region_id is None else str(dto.region_id),
        "country": None if dto.country_id is None else str(dto.country_id),
        "vehicle_class": dto.vehicle_class,
        "base_fare": dto.base_fare,
        "per_km_rate": dto.per_km_rate,
        "per_minute_rate": dto.per_minute_rate,
        "minimum_fare": dto.minimum_fare,
        "night_surcharge_pct": dto.night_surcharge_pct,
        "night_from": dto.night_from,
        "night_to": dto.night_to,
        "airport_surcharge": dto.airport_surcharge,
        "waiting_rate_per_minute": dto.waiting_rate_per_minute,
        "currency": dto.currency,
        "valid_from": dto.valid_from,
        "valid_to": dto.valid_to,
        "is_active": dto.is_active,
    }


def _audit(
    action: AuditAction,
    entity: str,
    public_id: UUID,
    *,
    principal: Principal | None,
    ip: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    record_audit(
        action,
        entity_type=entity,
        entity_id=str(public_id),
        actor_user_id=None if principal is None else principal.user_id,
        actor_role=None if principal is None else ",".join(sorted(principal.roles)),
        before=before,
        after=after,
        ip=ip,
        reason=reason,
    )


def _plain(payload: dict[str, Any]) -> dict[str, Any]:
    """An audit snapshot the log can serialise.

    `Decimal`, `date` and `UUID` all reach the audit sink and none of them is
    JSON. Stringifying here rather than in the sink keeps the conversion beside
    the values it converts, where a reader can see which figure became which
    string.
    """
    return {key: None if value is None else str(value) for key, value in payload.items()}


# -- corridors --------------------------------------------------------------


@transaction.atomic
def create_corridor(
    *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    dto = transport.create_corridor(**_corridor_fields(fields))
    payload = _named(dto)
    _audit(
        AuditAction.CATALOGUE_CREATED,
        "transfer_corridor",
        dto.public_id,
        principal=principal,
        ip=ip,
        after=_plain(payload),
    )
    return payload


@transaction.atomic
def update_corridor(
    public_id: UUID, *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    dto = transport.update_corridor(public_id, **_corridor_fields(fields))
    payload = _named(dto)
    _audit(
        AuditAction.CATALOGUE_UPDATED,
        "transfer_corridor",
        public_id,
        principal=principal,
        ip=ip,
        after=_plain(payload),
    )
    return payload


# -- tariffs ----------------------------------------------------------------


@transaction.atomic
def create_tariff(
    *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    dto = transport.create_tariff(**_tariff_fields(fields))
    payload = _tariff_payload(dto)
    _audit(
        AuditAction.CATALOGUE_CREATED,
        "transfer_tariff",
        dto.public_id,
        principal=principal,
        ip=ip,
        after=_plain(payload),
    )
    return payload


@transaction.atomic
def update_tariff(
    public_id: UUID, *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    dto = transport.update_tariff(public_id, **_tariff_fields(fields))
    payload = _tariff_payload(dto)
    _audit(
        AuditAction.CATALOGUE_UPDATED,
        "transfer_tariff",
        public_id,
        principal=principal,
        ip=ip,
        after=_plain(payload),
    )
    return payload


# -- the preview ------------------------------------------------------------


def preview_quote(payload: dict[str, Any]) -> dict[str, Any]:
    """§27.11's tool, over the tourist's own code path.

    What it adds is provenance, not arithmetic: every option names the rule
    that priced it and the rung it answered on, so an administrator can see
    *why* a corridor prices the way it does before saving a change that alters
    it.

    A preview is a read. It takes no lock, writes nothing and is not audited —
    §41.13 records administrative *actions*, and looking at a price is not one.
    """
    origin_id = _destination_id(payload["origin_destination"], field="origin_destination")
    target_id = _destination_id(payload["target_destination"], field="target_destination")
    places = catalogue.transfer_places("destination", [origin_id, target_id])

    origin = places.get(origin_id)
    target = places.get(target_id)
    if origin is None or target is None:
        raise NotFoundError()

    when = payload.get("pickup_at") or timezone.now()
    quote = transport.preview(
        LegRequest(
            reference="preview",
            origin=_endpoint(origin),
            target=_endpoint(target),
            depart_at=when,
            # §12.4 evaluates the night window against "pickup_at local time",
            # so the instant is converted into the origin's zone before it is
            # compared against a pair of `TIME` columns that carry no offset.
            pickup_local=when.astimezone(ZoneInfo(origin.timezone)).replace(tzinfo=None),
            pax=payload["pax"],
            luggage=payload["luggage"],
            distance_m=payload.get("distance_m"),
            travel_seconds=payload.get("travel_seconds"),
        )
    )

    return {
        "origin": origin.destination_name,
        "target": target.destination_name,
        "options": [
            {
                "vehicle_class": option.vehicle_class.code,
                "seats": option.vehicle_class.seats,
                "luggage": option.vehicle_class.luggage_capacity,
                "price": {
                    "amount": str(option.price.amount),
                    "currency": option.price.currency,
                },
                "breakdown": {
                    "base": str(option.breakdown.base),
                    "distance": str(option.breakdown.distance),
                    "time": str(option.breakdown.time),
                    "surcharges": str(option.breakdown.surcharges),
                },
                "matched_kind": str(option.match.kind),
                "matched_rule": option.match.rule_public_id,
                "matched_step": option.match.step,
            }
            for option in quote.options
        ],
    }


def _endpoint(place: catalogue.TransferPlace) -> LegEndpoint:
    return LegEndpoint(
        label=place.destination_name,
        destination_id=place.destination_id,
        region_id=place.region_id,
        country_id=place.country_id,
        is_gateway=place.is_gateway,
    )


# -- §27.7 providers ----------------------------------------------------------
#
# The same division of labour as the tariff console above. `provider` owns the
# table and the verification machine; this module turns the region slug an
# administrator typed into an id, renders it back, and writes the audit entry
# inside the transaction that made the change.


def _provider_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields = dict(payload)
    if "region" in fields:
        fields["region_id"] = _scope_id("region", fields.pop("region"))
    return fields


def _provider_payload(dto: ProviderDTO, *, regions: dict[int, str] | None = None) -> dict[str, Any]:
    names = regions if regions is not None else catalogue.region_keys([dto.region_id])
    return {
        "public_id": dto.public_id,
        "legal_name": dto.legal_name,
        "trading_name": dto.trading_name,
        "provider_type": dto.provider_type,
        "contact_email": dto.contact_email,
        "contact_phone": dto.contact_phone,
        # A region retired out from under a provider is a real state — no SQL
        # foreign key stops it (ADR 0012) — and an empty name says so.
        "region": names.get(dto.region_id, ""),
        "verify_status": dto.verify_status,
        "verified_at": dto.verified_at,
        "is_sellable": dto.is_sellable,
        "payout_account_ref": dto.payout_account_ref,
        "payout_currency": dto.payout_currency,
        "rating_avg": dto.rating_avg,
        "rating_count": dto.rating_count,
    }


@transaction.atomic
def create_provider(
    *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    dto = provider.create_provider(**_provider_fields(fields))
    payload = _provider_payload(dto)
    _audit(
        AuditAction.PROVIDER_CREATED,
        "provider",
        dto.public_id,
        principal=principal,
        ip=ip,
        after=_plain(payload),
    )
    return payload


@transaction.atomic
def update_provider(
    public_id: UUID, *, fields: dict[str, Any], principal: Principal | None, ip: str | None
) -> dict[str, Any]:
    before = _provider_payload(provider.get_provider(public_id))
    dto = provider.update_provider(public_id, **_provider_fields(fields))
    payload = _provider_payload(dto)
    _audit(
        AuditAction.PROVIDER_UPDATED,
        "provider",
        public_id,
        principal=principal,
        ip=ip,
        before=_plain(before),
        after=_plain(payload),
    )
    return payload


def list_providers(*, provider_type: str | None, verify_status: str | None) -> list[dict[str, Any]]:
    rows = provider.list_providers(provider_type=provider_type, verify_status=verify_status)
    regions = catalogue.region_keys([row.region_id for row in rows])
    return [_provider_payload(row, regions=regions) for row in rows]


def get_provider(public_id: UUID) -> dict[str, Any]:
    return _provider_payload(provider.get_provider(public_id))


@transaction.atomic
def change_provider_status(
    public_id: UUID,
    *,
    status: str,
    reason: str,
    principal: Principal | None,
    ip: str | None,
) -> dict[str, Any]:
    """§26.2's decision, audited once per declared edge it passed.

    One "verify" on a draft is three edges, and the log shows three entries in
    order. A compliance reviewer reading the trail sees a provider submitted,
    reviewed and approved — never one that skipped straight to approval.
    """
    change = provider.change_status(public_id, status, now=timezone.now())
    current = change.before
    for step in change.steps:
        _audit(
            AuditAction.PROVIDER_STATUS_CHANGED,
            "provider",
            public_id,
            principal=principal,
            ip=ip,
            before={"verify_status": current},
            after={"verify_status": step},
            reason=reason,
        )
        current = step
    return {
        "before": change.before,
        "steps": list(change.steps),
        "provider": _provider_payload(change.provider),
    }


@transaction.atomic
def assign_activity_provider(
    activity_public_id: UUID,
    *,
    provider_public_id: UUID,
    principal: Principal | None,
    ip: str | None,
) -> dict[str, Any]:
    """Give an activity the provider that sells it.

    Here because it is the one place both halves are visible: whether the
    provider may own an ACTIVITY (§7.5.3) is a `provider` question, and the
    column is `catalogue`'s. The write goes through `catalogue.services.update`,
    so it is audited like every other catalogue edit, with the before and after
    `provider_id` in the diff.
    """
    owner = provider.require_owner_of(provider_public_id, "ACTIVITY")
    catalogue.update(
        catalogue.entity_for("activity"),
        activity_public_id,
        fields={"provider_id": owner.id},
        principal=principal,
        ip=ip,
    )
    return {"activity": activity_public_id, "provider": _provider_payload(owner)}
