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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib.gis.geos import Point
from django.db import transaction

from apps.catalogue import repositories as repo
from apps.catalogue.domain import opening_hours
from apps.catalogue.domain.schedules import ScheduleError, ScheduleRule, occurrence_dates
from apps.catalogue.dto import ListingRefDTO
from apps.catalogue.models import (
    Accommodation,
    Activity,
    ActivitySchedule,
    Attraction,
    CancellationPolicy,
    Country,
    Destination,
    Market,
    Media,
    MediaOwnerType,
    Region,
    Tag,
)
from apps.catalogue.selectors import reference_q, visible
from apps.common.audit import AuditAction, record_audit
from apps.common.authz import Permission, Principal, Resource, Role
from apps.common.errors import NotFoundError, ValidationError
from apps.common.geo import COORDINATE_PRECISION, Coordinates
from apps.common.models import SoftDeleteModel

__all__ = [
    "CatalogueEntity",
    "ENTITIES",
    "entity_for",
    "REFERENCEABLE",
    "resolve_refs",
    "PlanningRef",
    "resolve_planning_ref",
    "ListingLocator",
    "resolve_listing_ref",
    "PlaceFacts",
    "ActivityFacts",
    "AttractionFacts",
    "place_facts",
    "activity_facts",
    "attraction_facts",
    "opening_status",
    "ScheduleFacts",
    "active_schedules",
    "resolve_references",
    "to_orm_fields",
    "SEED_FILES",
    "SeedResult",
    "load_seed",
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

    point_field: str | None = None
    """Where `latitude`/`longitude` land. `centroid` on a destination."""

    natural_key: str = "slug"
    """What a seed file identifies this entity by. See `load_seed`."""

    country_path: tuple[str, ...] = ()
    """Attribute chain from this row to the country whose bounds apply to it.

    Empty for an entity that carries no coordinate. The first element is
    always a key of `references`, which is what lets the country be found from
    the request body on a create — before the row it belongs to exists.
    """


ENTITIES: Mapping[str, CatalogueEntity] = MappingProxyType(
    {
        entity.key: entity
        for entity in (
            CatalogueEntity(
                "country",
                Country,
                Resource.COUNTRY,
                repo.create_country,
                repo.update_country,
                natural_key="iso_code",
            ),
            CatalogueEntity(
                "market",
                Market,
                Resource.MARKET,
                repo.create_market,
                repo.update_market,
                {"country": Country},
            ),
            CatalogueEntity(
                "region",
                Region,
                Resource.REGION,
                repo.create_region,
                repo.update_region,
                # Both, not just `market`. `region.country` is denormalised
                # (see the model), and the pair is held together by a composite
                # FOREIGN KEY, so a create naming a market in another country
                # is refused by PostgreSQL rather than silently stored.
                {"country": Country, "market": Market},
            ),
            CatalogueEntity(
                "destination",
                Destination,
                Resource.DESTINATION,
                repo.create_destination,
                repo.update_destination,
                {"region": Region},
                point_field="centroid",
                country_path=("region", "country"),
            ),
            CatalogueEntity("tag", Tag, Resource.TAG, repo.create_tag, repo.update_tag),
            CatalogueEntity(
                "cancellation_policy",
                CancellationPolicy,
                Resource.CANCELLATION_POLICY,
                repo.create_cancellation_policy,
                repo.update_cancellation_policy,
                natural_key="code",
            ),
            CatalogueEntity(
                "attraction",
                Attraction,
                Resource.ATTRACTION,
                repo.create_attraction,
                repo.update_attraction,
                {"destination": Destination},
                point_field="coordinates",
                country_path=("destination", "region", "country"),
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
                point_field="coordinates",
                country_path=("destination", "region", "country"),
            ),
            CatalogueEntity(
                "accommodation",
                Accommodation,
                Resource.ACCOMMODATION,
                repo.create_accommodation,
                repo.update_accommodation,
                {"destination": Destination},
                point_field="coordinates",
                country_path=("destination", "region", "country"),
            ),
        )
    }
)


def entity_for(key: str) -> CatalogueEntity:
    return ENTITIES[key]


def to_orm_fields(
    entity: CatalogueEntity,
    fields: Mapping[str, Any],
    *,
    existing: SoftDeleteModel | None = None,
) -> dict[str, Any]:
    """Wire shape to ORM shape: references resolved, degrees made a geometry.

    Both halves are here rather than in the serializer because the serializer
    is not the only entrance. §41.12's console path goes through it; Appendix
    C's seed loader does not, and the two must not be able to disagree about
    which of `latitude` and `longitude` is the `x` of a `Point`. Getting that
    backwards puts a Zanzibar hotel in the Gulf of Guinea, and every test that
    only checks a row was written still passes.

    `existing` is the row being updated, if there is one. It is needed only to
    find the country whose bounds apply when a PATCH moves a coordinate without
    restating the parent.
    """
    return _fold_coordinates(entity, resolve_references(entity, fields), existing=existing)


def _fold_coordinates(
    entity: CatalogueEntity,
    fields: dict[str, Any],
    *,
    existing: SoftDeleteModel | None = None,
) -> dict[str, Any]:
    """`latitude` + `longitude` become the entity's point column.

    Absent on a PATCH that does not touch the location, in which case the
    column is left alone rather than blanked — the serializer has already
    refused half a pair, so absence here means "not being changed".
    """
    if entity.point_field is None:
        return fields
    latitude = fields.pop("latitude", None)
    longitude = fields.pop("longitude", None)
    if latitude is None or longitude is None:
        return fields
    # `Coordinates` refuses an out-of-range or over-precise pair and is
    # `Decimal`-based, so it is asked before anything becomes a float. `Point`
    # takes floats because PostGIS stores double precision — that is a
    # property of the storage type, not a choice.
    #
    # Its refusal is a domain `ValueError` and becomes a 422 here. `domain/`
    # may not import the platform's error hierarchy, so the translation has to
    # happen at the first layer that can — and a latitude of 91 arriving as a
    # 500 would tell an administrator nothing about which field to fix. The
    # serializer's `DecimalField` bounds precision but not range: 91.0000000
    # is a perfectly well-formed decimal.
    try:
        checked = Coordinates(lat=Decimal(str(latitude)), lon=Decimal(str(longitude)))
    except ValueError as exc:
        raise ValidationError(str(exc), details=[{"field": "latitude", "issue": str(exc)}]) from exc
    _require_within_country(entity, checked, fields, existing)
    fields[entity.point_field] = Point(float(checked.lon), float(checked.lat), srid=4326)
    return fields


def _require_within_country(
    entity: CatalogueEntity,
    point: Coordinates,
    fields: Mapping[str, Any],
    existing: SoftDeleteModel | None,
) -> None:
    """Refuse a coordinate outside the bounding box of its own country.

    The error this catches is a swapped pair, and it is the one geographic
    mistake that looks exactly like success. `Coordinates` rejects a latitude
    of 91; it cannot reject a latitude of 39.19, which is a real latitude in
    Turkey and a Zanzibar *longitude*. Nothing downstream notices — the row
    writes, the audit entry records it, the API serves it, and the property
    appears on the map six thousand kilometres out to sea in the Gulf of
    Guinea. §13.2's confirmed-pin flow guards the tourist's own free entry;
    this guards curated data, which no tourist confirms.

    The box comes from the `country` row, never from a constant. §4.2 forbids
    this module knowing where the market is, and `test_it_loads_a_market_the_
    seed_files_never_mention` opens Kenya through this same code path.
    """
    country = _country_for(entity, fields, existing)
    if country is None or country.bounds.contains(point):
        return
    box = country.bounds
    raise ValidationError(
        f"Coordinate lies outside {country.iso_code}: "
        f"latitude must be between {box.min_lat} and {box.max_lat}, "
        f"longitude between {box.min_lon} and {box.max_lon}. "
        "Check that latitude and longitude have not been transposed.",
        details=[
            {"field": "latitude", "issue": f"{point.lat} is outside {country.iso_code}."},
            {"field": "longitude", "issue": f"{point.lon} is outside {country.iso_code}."},
        ],
    )


def _country_for(
    entity: CatalogueEntity,
    fields: Mapping[str, Any],
    existing: SoftDeleteModel | None,
) -> Country | None:
    """Walk `country_path` to the country this row belongs to.

    Two starting points, because a create has no row yet. On a create — and on
    an update that moves the row to a new parent — the first hop is the
    reference already resolved into `fields`, so the coordinate is checked
    against the country it is *becoming* part of rather than the one it is
    leaving. Otherwise the walk starts from the stored row.

    Returns `None` only when neither is available, which is a create whose
    required parent is missing. That request is about to fail on the parent;
    reporting a bounds error for it would name the wrong field.
    """
    if not entity.country_path:
        return None
    head, *rest = entity.country_path
    node: Any = fields.get(head) or (getattr(existing, head, None) if existing else None)
    for attribute in rest:
        if node is None:
            return None
        node = getattr(node, attribute, None)
    return node if isinstance(node, Country) else None


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
    dto = entity.create(**to_orm_fields(entity, fields))
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
    dto = entity.update(
        public_id,
        **to_orm_fields(entity, fields, existing=repo.reference(entity.model, public_id)),
    )
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


# ---------------------------------------------------------------------------
# The Appendix C seed set
# ---------------------------------------------------------------------------

#: Which file holds which entity, and the order they must be loaded in.
#:
#: The order is a real constraint, not a preference: a destination names its
#: region and an attraction names its destination, so the parent has to exist.
#: The numeric prefixes on the filenames say the same thing to anyone reading
#: the directory, and this mapping is what the loader actually obeys.
SEED_FILES: tuple[tuple[str, str], ...] = (
    ("01-countries", "country"),
    # ADR 0018. Between country and region, because a region now names its
    # market. Everything below it was renumbered rather than given a fractional
    # prefix: the numbers exist to tell a reader the order, and `015` would
    # have told them something false to save six renames.
    ("02-markets", "market"),
    ("03-regions", "region"),
    ("04-destinations", "destination"),
    ("05-tags", "tag"),
    ("06-attractions", "attraction"),
    ("07-accommodation", "accommodation"),
    # Last by number and independent of everything above it: the four §14.6
    # policies are referenced by `activity`, and activities are provider-
    # supplied rather than seeded (Appendix C as amended by ADR 0013). They are
    # seeded anyway because they are the vocabulary a provider picks from — a
    # portal offering an empty policy list is a portal nobody can list on.
    ("08-cancellation-policies", "cancellation_policy"),
)


#: The media seed, loaded separately from `SEED_FILES`.
#:
#: `media` is **not** a `CatalogueEntity` and cannot be one. `Media` extends
#: `TimestampedModel`, not `SoftDeleteModel`: it has no `public_id` for
#: `record_audit` to use as an entity id, and no `deleted_at` for
#: `find_by_natural_key` to filter on — both deliberate, because §7.3 makes a
#: media row identified by its content-hashed `file_key` and §35.7 says a
#: removed image is removed rather than retired.
#:
#: Forcing it into the registry would mean adding a `public_id` the model has
#: no use for, or special-casing the audit path for one entity. A separate
#: loader is the smaller lie: it is fifteen lines, it says plainly that media
#: is a different kind of thing, and it leaves the registry describing exactly
#: the entities that fit it.
MEDIA_SEED_FILE = "09-media"

#: Which table an `owner_type` names, so a seed row can say `"stone-town"`
#: instead of a primary key no person could write.
_MEDIA_OWNERS: Mapping[str, type[SoftDeleteModel]] = MappingProxyType(
    {
        MediaOwnerType.MARKET.value: Market,
        MediaOwnerType.DESTINATION.value: Destination,
        MediaOwnerType.ATTRACTION.value: Attraction,
        MediaOwnerType.ACTIVITY.value: Activity,
        MediaOwnerType.ACCOMMODATION.value: Accommodation,
    }
)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """What one file did. Reported per entity so a re-run is legible."""

    entity: str
    created: int
    updated: int

    def __str__(self) -> str:
        return f"{self.entity}: {self.created} created, {self.updated} updated"


def load_media_seed(rows: Sequence[Mapping[str, Any]]) -> SeedResult:
    """Load `media`, resolving each row's owner by slug.

    Idempotent by `(owner_type, owner_id, file_key)`, which is the natural key
    §35.7 gives this table: the key is a content hash, so the same photograph
    re-seeded is the same row rather than a duplicate.

    A row naming an owner that does not exist is an error rather than a skip.
    A gallery silently missing its hero is the failure this whole phase keeps
    finding, and a seed loader is a cheap place to refuse it.
    """
    created = updated = 0
    for row in rows:
        fields = dict(row)
        owner_type = str(fields.pop("owner_type"))
        owner_slug = str(fields.pop("owner"))

        model = _MEDIA_OWNERS.get(owner_type)
        if model is None:
            raise ValidationError(f"media: unknown owner_type {owner_type!r}")

        # The same lookup the entity loader uses, for the same reason: a seed
        # file identifies a row the way a person does, and a retired owner
        # must not be resurrected by re-seeding its photographs.
        owner = repo.find_by_natural_key(model, "slug", owner_slug)
        if owner is None:
            raise ValidationError(f"media: no live {owner_type} with slug {owner_slug!r}")

        existing = Media.objects.filter(
            owner_type=owner_type, owner_id=owner.pk, file_key=fields["file_key"]
        ).first()
        if existing is None:
            Media.objects.create(owner_type=owner_type, owner_id=owner.pk, **fields)
            created += 1
        else:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.save()
            updated += 1

    return SeedResult("media", created, updated)


def load_seed(
    entity_key: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    principal: Principal | None = None,
    ip: str | None = None,
) -> SeedResult:
    """Load one entity's seed rows. Idempotent, and audited like any write.

    **Idempotent by natural key.** `make seed` is run on every fresh checkout
    and again whenever the data changes, so a second run must be a no-op and
    not a unique-constraint failure or forty duplicate hotels. A seed file
    cannot carry `public_id` — it is written before the row exists and lives in
    git, where a UUID would be invented by hand and change on every re-seed —
    so the row identifies itself by ISO code or slug, and an existing live row
    with that key is updated rather than inserted.

    **Through the same audited path as the console.** Appendix C says the seed
    set is *"loadable through the admin console"*, and §41.13 audits every
    administrative action. A bulk `Model.objects.create()` here would be faster
    and would leave sixty-nine catalogue rows with no record of where they came
    from — which is the question somebody asks first when one of them is wrong.

    **A retired row stays retired.** `find_by_natural_key` looks only at live
    rows, so re-seeding does not resurrect a destination an administrator
    deliberately withdrew; §7.7 releases the slug so the name can be reused,
    and a new row is what the loader would then create. Withdrawing something
    the seed file still lists is a decision, and re-running a loader is not the
    place to reverse it.

    References are by natural key too — `"region": "zanzibar-north"` — because
    a file that had to name a UUID could not be written by a person.
    """
    entity = entity_for(entity_key)
    created = updated = 0
    for row in rows:
        fields = _resolve_natural_references(entity, row)
        key = fields.pop(entity.natural_key, None) or row.get(entity.natural_key)
        existing = repo.find_by_natural_key(entity.model, entity.natural_key, str(key))
        if existing is None:
            create(entity, fields={**fields, entity.natural_key: key}, principal=principal, ip=ip)
            created += 1
        else:
            update(entity, existing.public_id, fields=fields, principal=principal, ip=ip)
            updated += 1
    return SeedResult(entity_key, created, updated)


def _resolve_natural_references(entity: CatalogueEntity, row: Mapping[str, Any]) -> dict[str, Any]:
    """Swap each parent's slug or ISO code for that parent's `public_id`.

    Turning a natural key into a UUID and letting `to_orm_fields` turn it back
    into a row looks indirect, and is deliberate: it means the seed path and
    the API path converge before anything is written, so a rule that holds for
    a console write holds for a seeded one. The alternative is a second write
    path with its own bugs and its own audit behaviour.
    """
    fields = dict(row)
    for name, model in entity.references.items():
        value = fields.get(name)
        if value is None:
            continue
        key = "iso_code" if model is Country else "slug"
        parent = repo.find_by_natural_key(model, key, str(value))
        if parent is None:
            raise ValidationError(
                f"seed row names a {name} that does not exist: {value!r}",
                details=[{"field": name, "issue": "No such row."}],
            )
        fields[name] = parent.public_id
    return fields


# ---------------------------------------------------------------------------
# The read seam ADR 0012 promised — resolving another module's stored ids.
# ---------------------------------------------------------------------------

#: The tables another module may hold an id for, by the name it uses in its
#: own columns. `trip.itinerary_item` stores `accommodation_id`, `activity_id`,
#: `attraction_id` and two `*_destination_id`s; every one of them resolves
#: through here.
#:
#: Stated as an explicit map rather than derived from `ENTITIES`: those are the
#: tables an administrator may *write*, which is a different question, and
#: letting one set answer for the other would silently widen this the next time
#: a curated table is added.
REFERENCEABLE: Mapping[str, type[SoftDeleteModel]] = MappingProxyType(
    {
        "destination": Destination,
        "attraction": Attraction,
        "activity": Activity,
        "accommodation": Accommodation,
    }
)


def resolve_refs(kind: str, ids: Sequence[int]) -> dict[int, ListingRefDTO]:
    """Name the catalogue rows another module is holding ids for — ADR 0012.

    ADR 0012 has a cross-module reference stored as a plain integer, because a
    `ForeignKey` would install a traversable attribute and take the §6.4
    boundary with it. It then says the row is read back through "a service call
    returning a DTO". Nothing implemented that call, so `catalogue.services`
    had no read interface at all and `trip` had no supported way to turn
    `destination_id` into "Stone Town".

    **The id is an input, never an output.** §7.2 forbids returning sequential
    integers to *clients*; this is a module boundary, and the caller already
    holds the integer because it is in its own column. What comes back is a
    DTO whose identity is a `public_id`, so the integer stops here.

    **One query, whatever the list.** The caller passes every id it needs at
    once. An itinerary has a reference on most of its rows, and a per-row
    lookup would make rendering a fortnight's trip an N+1 against four tables.

    **Visibility is deliberately not applied.** A trip may legitimately hold an
    item whose listing has since been withdrawn — that is precisely what VR-09
    exists to report. Filtering here would leave the item nameless instead, and
    a finding that cannot say *which* listing is unavailable is not actionable.
    Soft-deleted rows are excluded, because `deleted_at` means the row is gone
    rather than hidden, and `all_objects` is the administrative path.

    A missing id is simply absent from the result. The caller knows what it
    asked for and is the only layer that can decide what a dangling reference
    means.
    """
    try:
        model = REFERENCEABLE[kind]
    except KeyError as exc:
        raise ValidationError(
            f"{kind!r} is not a referenceable catalogue table; "
            f"expected one of {sorted(REFERENCEABLE)}."
        ) from exc

    wanted = {int(value) for value in ids if value is not None}
    if not wanted:
        return {}

    # A destination has its own `timezone`; everything beneath one inherits
    # its destination's, which is why this reaches through rather than
    # selecting a column that is not there.
    zone = "timezone" if model is Destination else "destination__timezone"
    rows = model.objects.filter(pk__in=wanted).values_list("pk", "public_id", "slug", "name", zone)
    return {
        pk: ListingRefDTO(public_id=public_id, slug=slug, name=name, timezone=timezone)
        for pk, public_id, slug, name, timezone in rows
    }


@dataclass(frozen=True, slots=True)
class ListingLocator:
    """A catalogue listing a tourist named, as the module storing it needs it.

    The counterpart of `resolve_refs` for the three listing tables, and the
    same shape of thing as `PlanningRef` is for a destination: a name in, a
    storable id out.

    **Why this exists at all.** ADR 0012 has `trip.itinerary_item` keep
    `activity_id` as a plain integer, and `AddItemSerializer` originally took
    that integer straight from the request — which no client can supply. §7.2
    forbids returning sequential integers to clients, so the catalogue API
    exposes `public_id` and `slug` and nothing else; the integer a tourist
    would have had to send does not exist outside the database. The endpoint
    was therefore uncallable by any real client, which is why every "Add to
    trip" button in the web app was still disabled.

    `storage_id` carries the deliberate name `PlanningRef` uses for the same
    reason: the integer legitimately crosses a *module* boundary and must never
    cross the API one, and the DTO leak guard in `trip/tests/test_selectors.py`
    fails the build on any field ending `_id` that reaches a response.

    `name` comes back because the caller titles the itinerary item with it. A
    client-supplied title would let two tourists' plans disagree about what the
    same listing is called.
    """

    storage_id: int
    public_id: UUID
    slug: str
    name: str


def resolve_listing_ref(kind: str, reference: str | UUID, *, today: date) -> ListingLocator | None:
    """Resolve an activity, attraction or accommodation by slug or UUID.

    **Visibility applies**, for the same reason it does in
    `resolve_planning_ref` and not in `resolve_refs`: this is a tourist
    choosing something to add, which is a public read. A listing that is
    withdrawn, or whose market has not launched, must be indistinguishable
    from one that never existed (§30.3). A trip that *already* holds a
    withdrawn listing keeps naming it — that is VR-09's job, and it reads
    through `resolve_refs`, which deliberately does not filter.

    `destination` is rejected. It resolves through `resolve_planning_ref`,
    which returns the currency and timezone a trip cannot be opened without,
    and offering a second door to the same table would let a caller take the
    one that answers less.

    Returns `None` rather than raising, so the caller chooses between a 404 and
    a field-level validation error.
    """
    if kind == "destination" or kind not in REFERENCEABLE:
        raise ValidationError(
            f"{kind!r} is not an addable catalogue listing; "
            f"expected one of {sorted(set(REFERENCEABLE) - {'destination'})}."
        )

    row = (
        visible(REFERENCEABLE[kind].objects.all(), today=today)
        .filter(reference_q(reference))
        .only("id", "public_id", "slug", "name")
        .first()
    )
    if row is None:
        return None
    return ListingLocator(
        storage_id=int(row.id),
        public_id=row.public_id,
        slug=row.slug,
        name=row.name,
    )


@dataclass(frozen=True, slots=True)
class PlanningRef:
    """A destination, as the module that plans against it needs to store it.

    The mirror of `resolve_refs`: that turns a stored id into a name, this
    turns a name a tourist typed into something storable.

    **`storage_id` is deliberately not called `id`.** ADR 0012 has `trip`
    keep `destination_id` as a plain integer, so the integer legitimately
    crosses this boundary — §7.2 forbids returning sequential integers *to
    clients*, and a module is not a client. Naming it for its one purpose
    keeps that legitimacy narrow, and it ends in `_id`, so the DTO leak guard
    in `trip/tests/test_selectors.py` fails the build if it ever reaches a
    response.

    `default_currency` is here because §7.5.10 makes `trip.currency` NOT NULL
    and §7.2 pairs a currency with every money column: a trip has to be opened
    with one, and the destination is where §4.2 says it comes from.

    `timezone` is here because "is this date in the past" has no answer without
    it. A trip starting today in Zanzibar is still yesterday in UTC for three
    hours every night, so a server-side `date.today()` would refuse a
    perfectly ordinary booking made late in the evening — and would do it
    inconsistently, depending on the hour.
    """

    storage_id: int
    public_id: UUID
    slug: str
    name: str
    default_currency: str
    timezone: str


def resolve_planning_ref(reference: str | UUID, *, today: date) -> PlanningRef | None:
    """Resolve a destination slug or UUID for a module that plans against it.

    **Visibility applies here, unlike in `resolve_refs`.** The two ask
    different questions. `resolve_refs` names a row a trip already references,
    and must still name one that has been withdrawn so VR-09 can say which
    listing became unavailable. This is a tourist choosing a destination to
    open a trip against, which is a public read: a destination that is not
    visible today — inactive, or in a market whose `launch_date` has not
    arrived — must be indistinguishable from one that does not exist (§30.3).

    Returns `None` rather than raising, so the caller decides between 404 and
    a field-level validation error.
    """
    row = (
        visible(Destination.objects.all(), today=today)
        .filter(reference_q(reference))
        .only("id", "public_id", "slug", "name", "default_currency", "timezone")
        .first()
    )
    if row is None:
        return None
    return PlanningRef(
        storage_id=int(row.id),
        public_id=row.public_id,
        slug=row.slug,
        name=row.name,
        default_currency=row.default_currency,
        timezone=row.timezone,
    )


# ---------------------------------------------------------------------------
# Planning facts — everything §10.4 and §10.6 need to know about a listing.
# ---------------------------------------------------------------------------
#
# `resolve_refs` names a row; this describes one. They are separate because
# they are read at different moments and cost different amounts: rendering a
# stored itinerary needs only names, and generating one needs coordinates,
# capacities and cutoffs. Merging them would put the expensive read on the
# cheap path.
#
# Visibility is **not** applied here, for the same reason `resolve_refs`
# does not apply it: a trip may hold an item whose listing has been withdrawn,
# and VR-09's job is to say so by name. `is_active` is returned instead, so the
# validator decides rather than the query.


@dataclass(frozen=True, slots=True)
class PlaceFacts:
    """Where a listing is, whether it is still on sale, and in which zone.

    `timezone` is the destination's IANA zone — its own for a destination, its
    parent's for everything beneath one. §10.6's VR-01 turns a trip's *dates*
    into an instant range and §15.2 evaluates opening hours locally, so a
    planner that assumed UTC would move every boundary by up to half a day.
    It is carried here rather than fetched separately because the row is
    already being read.
    """

    storage_id: int
    slug: str
    name: str
    coordinates: Coordinates
    is_active: bool
    timezone: str


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    """§7.5.9, as §10.4's sequencing and §10.6's VR-05, VR-06 and VR-15 read it."""

    place: PlaceFacts
    duration_minutes: int
    min_pax: int
    max_pax: int
    min_age: int | None
    booking_cutoff_hours: int
    price_per_person: Decimal
    price_per_group: Decimal | None
    currency: str


@dataclass(frozen=True, slots=True)
class AttractionFacts:
    """§7.5.6's attraction, as VR-12 and §10.4's flexible placement read it.

    `visit_minutes` is nullable in the schema and §15.1 calls it a
    *recommended* duration, so an attraction without one is placed as a
    zero-length anchor rather than being given an invented length.
    """

    place: PlaceFacts
    visit_minutes: int | None


def _place(row: Any) -> PlaceFacts:
    # A destination carries `centroid` and its own zone; everything beneath one
    # carries `coordinates` and inherits its destination's.
    point = getattr(row, "coordinates", None) or row.centroid
    zone = getattr(row, "timezone", None) or row.destination.timezone
    return PlaceFacts(
        storage_id=int(row.id),
        slug=row.slug,
        name=row.name,
        coordinates=Coordinates(
            lat=Decimal(str(round(point.y, COORDINATE_PRECISION))),
            lon=Decimal(str(round(point.x, COORDINATE_PRECISION))),
        ),
        is_active=row.is_active,
        timezone=zone,
    )


def _rows(model: Any, ids: set[int]) -> Any:
    """`select_related` only where a zone has to be reached for.

    `destination` has its own `timezone`; the three tables beneath it reach
    through to their destination for one, and without this that is a query per
    row on the exact path `place_facts` exists to keep flat.
    """
    queryset = model.objects.filter(pk__in=ids)
    if model is Destination:
        return queryset
    return queryset.select_related("destination")


def place_facts(kind: str, ids: Sequence[int]) -> dict[int, PlaceFacts]:
    """Coordinates for rows another module holds ids for — one query per kind.

    §10.4 compares locations and asks for travel between them; this is where
    the coordinates come from. `trip` never reads a geometry column itself,
    which is what keeps §13.1's "geography, never planar" a decision made in
    one module.
    """
    try:
        model = REFERENCEABLE[kind]
    except KeyError as exc:
        raise ValidationError(
            f"{kind!r} is not a referenceable catalogue table; "
            f"expected one of {sorted(REFERENCEABLE)}."
        ) from exc
    wanted = {int(value) for value in ids if value is not None}
    if not wanted:
        return {}
    return {row.id: _place(row) for row in _rows(model, wanted)}


def activity_facts(ids: Sequence[int]) -> dict[int, ActivityFacts]:
    """One query. VR-05, VR-06 and VR-15 each need a different column of this,
    and fetching them separately would be three passes over the same rows."""
    wanted = {int(value) for value in ids if value is not None}
    if not wanted:
        return {}
    return {
        row.id: ActivityFacts(
            place=_place(row),
            duration_minutes=row.duration_minutes,
            min_pax=row.min_pax,
            max_pax=row.max_pax,
            min_age=row.min_age,
            booking_cutoff_hours=row.booking_cutoff_hours,
            price_per_person=row.price_per_person,
            price_per_group=row.price_per_group,
            currency=row.currency,
        )
        for row in _rows(Activity, wanted)
    }


def attraction_facts(ids: Sequence[int]) -> dict[int, AttractionFacts]:
    wanted = {int(value) for value in ids if value is not None}
    if not wanted:
        return {}
    return {
        row.id: AttractionFacts(place=_place(row), visit_minutes=row.visit_minutes)
        for row in _rows(Attraction, wanted)
    }


@dataclass(frozen=True, slots=True)
class ScheduleFacts:
    """§16.2's recurring rule, already expanded into the local dates it runs.

    **The expansion happens here, not in the caller.** `domain.schedules` owns
    what a weekday mask means — bit 0 is Monday — and a mask read Sunday-first
    shifts every departure by a day while looking entirely plausible in a
    console. `inventory` may not import that module (contract
    `private-catalogue`), and it should not want to: the recurrence is the
    catalogue's rule and the departure is inventory's row.

    `local_dates` are dates, and `start_time` is a *local wall time* (§16.2).
    Resolving the two into an instant needs the destination's zone and its DST
    history; `timezone` comes along for exactly that, and doing the resolution
    is the materialiser's job.
    """

    schedule_id: int
    activity_id: int
    start_time: time
    capacity: int
    timezone: str
    local_dates: tuple[date, ...]


def active_schedules(
    *, start: date, horizon_days: int, after: int = 0, limit: int = 500
) -> list[ScheduleFacts]:
    """Every live schedule and the dates it runs, for §8.8's nightly job.

    Paged by ascending id rather than offset: the job runs for minutes over a
    growing table, and an `OFFSET` page shifts under an insert in a way that
    silently skips a schedule. `after` is the last id of the previous page.

    Soft-deleted and inactive schedules are excluded here rather than by the
    caller. §16.2 lets a provider retire a rule without touching the departures
    it already produced, and "what should generate tomorrow" is the catalogue's
    question — a filter in `inventory` would be a second answer to it.

    A rule the domain refuses — a zero mask, an inverted window — is skipped
    rather than raised. Both are impossible under `activity_schedule`'s CHECK
    constraints, so one appearing means a constraint was bypassed; failing the
    whole nightly job over one bad row would stop every other activity's
    calendar from extending, which is a much larger outage than one activity
    with no dates.

    A schedule whose validity window has passed yields an empty tuple rather
    than being filtered out, so a caller counting schedules and a caller
    counting departures agree about what was examined.
    """
    rows = (
        ActivitySchedule.objects.filter(is_active=True, activity__is_active=True, id__gt=after)
        .select_related("activity__destination")
        .order_by("id")[:limit]
    )
    out: list[ScheduleFacts] = []
    for row in rows:
        try:
            rule = ScheduleRule(
                weekday_mask=row.weekday_mask,
                start_time=row.start_time,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
            )
        except ScheduleError:
            continue
        out.append(
            ScheduleFacts(
                schedule_id=row.id,
                activity_id=row.activity_id,
                start_time=row.start_time,
                capacity=row.capacity,
                timezone=row.activity.destination.timezone,
                local_dates=occurrence_dates(rule, start=start, horizon_days=horizon_days),
            )
        )
    return out


def opening_status(when: Sequence[tuple[int, datetime]]) -> dict[int, bool | None]:
    """VR-12, evaluated here rather than in the module that asks.

    §15.2 puts opening hours in a fixed JSONB schema and evaluates them **in
    the destination's own timezone**, and `domain.opening_hours` already
    implements that, tested. `trip` asks "was it open then" and this answers;
    a copy of the rule over there would be a second implementation of
    something subtle — a range crossing midnight belongs to the previous local
    day — and the two would drift.

    `None` means the attraction publishes no hours (§15.2), which is not the
    same as closed and is why the return type is three-valued. VR-12 warns on
    `False` alone.

    One query for the whole set, with the destination's timezone joined in:
    resolving a zone per item is the N+1 this signature exists to prevent.
    """
    if not when:
        return {}
    rows = {
        row.id: row
        for row in Attraction.objects.filter(pk__in={i for i, _ in when}).select_related(
            "destination"
        )
    }
    out: dict[int, bool | None] = {}
    for attraction_id, instant in when:
        row = rows.get(attraction_id)
        if row is None or not row.opening_hours:
            out[attraction_id] = None
            continue
        try:
            hours = opening_hours.parse_opening_hours(row.opening_hours)
            out[attraction_id] = opening_hours.is_open_at(
                hours, instant, tz=ZoneInfo(row.destination.timezone)
            )
        except (opening_hours.OpeningHoursError, ValueError):
            # Unparseable hours are "not published" rather than "closed". A
            # malformed row is an administrator's problem, and warning the
            # tourist that an attraction may be shut would be reporting our
            # data error as their planning error.
            out[attraction_id] = None
    return out
