"""catalogue module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). All ORM writes; returns DTOs.

Everything here is an administrator write (§27.8). There is no provider write
surface in the catalogue at all: accommodation is curated location reference
data since ADR 0013, and activity listings arrive through the provider portal
in Phase 11, against these same primitives.

Three properties hold for every function in this module.

**`full_clean()` before every save.** §8.6 puts validation in the model layer so
that the console, the API and the seed loader cannot disagree about what a valid
row is. `apps.catalogue.validators` wraps the domain functions precisely so this
call reaches them; skipping it would leave a timezone or a cancellation tier
validated on one path and not another.

**Explicit writable field sets.** Each function names the fields it will accept
and refuses anything else, rather than iterating over whatever the caller
passed. A repository that `setattr`s arbitrary keys is a mass-assignment hole
that turns any future admin serializer bug into a way to set `deleted_at`,
`public_id` or `feature_rank` from the wire.

**Soft deletion, never `DELETE`.** §7.7: rows are retired by `deleted_at`, and
the partial unique indexes release the slug when they are. `hard_delete` exists
on the model for tests and data repair, and is deliberately not re-exported
here.

Nothing in this module filters by visibility. That is not an oversight: an
administrator manages rows that are not public — that is most of what §27.8 is
for — and `selectors.visible` is the public path. The two are separated so that
neither has to remember to be the other.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from django.contrib.gis.geos import Point
from django.db import transaction
from django.db.models import Model

from apps.catalogue.dto import (
    AccommodationDTO,
    ActivityDTO,
    AttractionDTO,
    CountryDTO,
    DestinationDTO,
    RegionDTO,
    TagDTO,
)
from apps.catalogue.models import (
    Accommodation,
    Activity,
    Attraction,
    Country,
    Destination,
    Region,
    Tag,
)
from apps.catalogue.selectors import (
    to_accommodation_dto,
    to_activity_dto,
    to_attraction_dto,
    to_country_dto,
    to_destination_dto,
    to_region_dto,
    to_tag_dto,
)
from apps.common.models import SoftDeleteModel

__all__ = [
    "UnwritableFieldError",
    "writable_fields",
    "snapshot",
    "reference",
    "find_by_natural_key",
    "create_country",
    "update_country",
    "create_region",
    "update_region",
    "create_destination",
    "update_destination",
    "create_attraction",
    "update_attraction",
    "create_activity",
    "update_activity",
    "create_accommodation",
    "update_accommodation",
    "create_tag",
    "update_tag",
    "set_active",
    "soft_delete",
    "restore",
]

_M = TypeVar("_M", bound=Model)


class UnwritableFieldError(ValueError):
    """A field was offered that this repository will not write.

    Raised rather than ignored. Silently dropping an unknown key means an admin
    form that stops saving a field reports success, and the administrator finds
    out when a tourist does.
    """


#: What an administrator may set, per entity. `public_id`, `deleted_at`,
#: `created_at`, `updated_at` and every denormalised projection are absent by
#: design — `rating_avg` and `rating_count` are written by `review`'s domain
#: event and by nothing else (§16.5).
_WRITABLE: Mapping[type[Model], frozenset[str]] = {
    Country: frozenset(
        {
            "iso_code",
            "name",
            "default_currency",
            "default_timezone",
            "is_active",
            # Writable, so opening a market outside Tanzania is a console form
            # rather than a migration (§4.2). Being writable also makes it
            # audited: `snapshot` derives from this set, so widening a bounding
            # box leaves an entry saying who widened it and by how much.
            "min_latitude",
            "min_longitude",
            "max_latitude",
            "max_longitude",
        }
    ),
    Region: frozenset({"country", "name", "slug", "is_active"}),
    Destination: frozenset(
        {
            "region",
            "name",
            "slug",
            "summary",
            "description",
            "centroid",
            "is_gateway",
            "gateway_type",
            "gateway_code",
            "timezone",
            "default_currency",
            "launch_date",
            "feature_rank",
            "is_active",
        }
    ),
    Attraction: frozenset(
        {
            "destination",
            "name",
            "slug",
            "summary",
            "description",
            "coordinates",
            "opening_hours",
            "entrance_fee",
            "fee_currency",
            "visit_minutes",
            "tags",
            "accessibility_notes",
            "feature_rank",
            "is_active",
        }
    ),
    Activity: frozenset(
        {
            "provider_id",
            "destination",
            "attraction",
            "name",
            "slug",
            "summary",
            "description",
            "coordinates",
            "meeting_point_text",
            "duration_minutes",
            "price_per_person",
            "price_per_group",
            "currency",
            "min_pax",
            "max_pax",
            "min_age",
            "requirements",
            "inclusions",
            "exclusions",
            "cancellation_policy",
            "booking_cutoff_hours",
            "confirmation_mode",
            "tags",
            "feature_rank",
            "is_active",
        }
    ),
    # ADR 0013. The set is short because the table is: a location record has no
    # provider, no rate, no capacity, no policy and no rating. There is nothing
    # here for an administrator to get commercially wrong.
    Accommodation: frozenset(
        {
            "destination",
            "name",
            "slug",
            "summary",
            "description",
            "property_type",
            "coordinates",
            "address_line",
            "check_in_time",
            "check_out_time",
            "feature_rank",
            "is_active",
        }
    ),
    Tag: frozenset({"slug", "label", "sort_order", "is_active"}),
}


def writable_fields(model: type[Model]) -> frozenset[str]:
    """What an administrator may set on this entity.

    Exposed because §41.13 requires the audit entry to carry before and after
    *state*, and the state worth recording is exactly the state an
    administrator could have changed. Deriving the snapshot from this set
    rather than from a second hand-maintained list means a field added here
    starts being audited in the same commit that makes it writable.
    """
    return _WRITABLE[model]


def _json_safe(value: Any) -> Any:
    """One field value, as something a `JSONField` can hold.

    A related row becomes its `public_id` rather than its primary key. §7.2
    keeps sequential integers off the wire, and an audit entry is read by a
    person: `"region": "a1b2..."` can be pasted into the console, where
    `"region": 41` names a row the reader cannot look up.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        # Decimal is not JSON-serialisable and float would round it. §7.2
        # forbids float for money anywhere, and an audit entry is where a
        # disputed amount is checked.
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Model):
        return str(value.public_id)  # type: ignore[attr-defined]
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Point):
        # Recorded as the pair a person reads and a map accepts, not as WKB.
        return {"lat": str(Decimal(str(value.y))), "lon": str(Decimal(str(value.x)))}
    return str(value)


def snapshot(model: type[Model], public_id: UUID) -> dict[str, Any] | None:
    """The writable state of one row, or `None` if there is no such row.

    §41.13 requires every administrative action to carry before and after
    state. This is that state, and it is derived from `_WRITABLE` rather than
    from a second list, so a newly writable field is audited by the commit
    that makes it writable.

    Reads through `all_objects`: the `before` half of a restore is a
    soft-deleted row, and a snapshot that could not see one would record the
    interesting case as an empty dict.
    """
    try:
        row = _get(model, public_id)
    except model.DoesNotExist:  # type: ignore[attr-defined]
        return None
    return {name: _json_safe(getattr(row, name)) for name in sorted(_WRITABLE[model])}


def reference(model: type[_M], public_id: UUID) -> _M | None:
    """One row named by another row's field, or `None`.

    The wire carries a reference as the target's `public_id` (§7.2); the ORM
    wants the instance. This is the only place that conversion happens, so a
    dangling reference is one failure with one message rather than a different
    one per entity.

    Reads through `all_objects` deliberately. An administrator repairing a
    hierarchy attaches a destination to a region that is currently retired,
    and refusing that would make a soft-deleted parent a dead end rather than
    a reversible decision.
    """
    try:
        return _get(model, public_id)
    except model.DoesNotExist:  # type: ignore[attr-defined]
        return None


def find_by_natural_key(model: type[_M], field: str, value: str) -> _M | None:
    """The live row a seed file names, or `None`.

    Seed data cannot carry `public_id`: the file is written before the row
    exists and is checked into git, where a generated UUID would either be
    invented by hand or change on every re-seed. So a seed row identifies
    itself the way a person does — by ISO code or by slug — and this is the
    only place that identification happens.

    Restricted to live rows on purpose. A soft-deleted destination was retired
    by an administrator, and re-running the seed loader must not quietly undo
    that; §7.7 releases the slug precisely so the name can be reused.
    """
    return model._default_manager.filter(**{field: value, "deleted_at__isnull": True}).first()


def _check(model: type[Model], fields: Mapping[str, Any]) -> None:
    offered = set(fields)
    allowed = _WRITABLE[model]
    unwritable = offered - allowed
    if unwritable:
        raise UnwritableFieldError(
            f"{model.__name__} will not accept {sorted(unwritable)}; "
            f"writable fields are {sorted(allowed)}"
        )


def _create(model: type[_M], fields: Mapping[str, Any]) -> _M:
    """Build, validate, save. §8.6, and §27.8's console path in particular."""
    _check(model, fields)
    row = model(**fields)
    row.full_clean()
    row.save()
    return row


def _update(row: _M, fields: Mapping[str, Any]) -> _M:
    """Apply a partial update. Absent keys are left alone, not blanked.

    `full_clean` runs over the whole row rather than the changed fields,
    because most of the interesting rules are cross-field — an entrance fee
    without a currency, a `max_pax` below `min_pax` — and validating only what
    changed would pass every one of them.
    """
    _check(type(row), fields)
    for name, value in fields.items():
        setattr(row, name, value)
    row.full_clean()
    row.save()
    return row


def _get(model: type[_M], public_id: UUID) -> _M:
    """The row, deleted or not.

    Administrators act on soft-deleted rows — restoring one is the obvious case
    — so this reads through `all_objects`. The public path is
    `selectors.visible`, and the separation is the point.
    """
    return model.all_objects.get(public_id=public_id)  # type: ignore[attr-defined,no-any-return]


# ---------------------------------------------------------------------------
# Countries and regions
# ---------------------------------------------------------------------------


@transaction.atomic
def create_country(**fields: Any) -> CountryDTO:
    """§7.3's `country`, and the first row §41.12 asks an administrator for.

    `default_currency` and `default_timezone` are defaults a destination may
    override (§4.2), not constraints on it: a country wide enough to span two
    zones has destinations that disagree with it, and the destination wins.
    """
    return to_country_dto(_create(Country, fields))


@transaction.atomic
def update_country(public_id: UUID, **fields: Any) -> CountryDTO:
    return to_country_dto(_update(_get(Country, public_id), fields))


@transaction.atomic
def create_region(**fields: Any) -> RegionDTO:
    return to_region_dto(_create(Region, fields))


@transaction.atomic
def update_region(public_id: UUID, **fields: Any) -> RegionDTO:
    return to_region_dto(_update(_get(Region, public_id), fields))


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


@transaction.atomic
def create_destination(**fields: Any) -> DestinationDTO:
    """§7.5.6.

    Note that `is_active` defaults to `False` on the model and is not defaulted
    to `True` here. §7.5.6 wants a new market invisible until somebody says
    otherwise; a repository that flipped it would make §41.12's Arusha test
    pass for the wrong reason.
    """
    return to_destination_dto(_create(Destination, fields))


@transaction.atomic
def update_destination(public_id: UUID, **fields: Any) -> DestinationDTO:
    return to_destination_dto(_update(_get(Destination, public_id), fields))


# ---------------------------------------------------------------------------
# Attractions
# ---------------------------------------------------------------------------


@transaction.atomic
def create_attraction(**fields: Any) -> AttractionDTO:
    return to_attraction_dto(_create(Attraction, fields))


@transaction.atomic
def update_attraction(public_id: UUID, **fields: Any) -> AttractionDTO:
    return to_attraction_dto(_update(_get(Attraction, public_id), fields))


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@transaction.atomic
def create_activity(**fields: Any) -> ActivityDTO:
    """§16.1.

    `provider_id` is writable and is a plain integer (ADR 0012). It stays
    absent on the admin path until `provider` exists in Phase 6; the field is
    listed so that the portal in Phase 11 does not need a second repository.
    """
    return to_activity_dto(_create(Activity, fields))


@transaction.atomic
def update_activity(public_id: UUID, **fields: Any) -> ActivityDTO:
    return to_activity_dto(_update(_get(Activity, public_id), fields))


# ---------------------------------------------------------------------------
# Accommodation
# ---------------------------------------------------------------------------


@transaction.atomic
def create_accommodation(**fields: Any) -> AccommodationDTO:
    """§7.5.7 as amended — ADR 0013.

    This is also the seed loader's path (Appendix C). Seeding roughly forty
    known Zanzibar properties is correct precisely because the row asserts
    nothing that only the property's owner could assert: a name, a type, a
    coordinate and two wall-clock times.
    """
    return to_accommodation_dto(_create(Accommodation, fields))


@transaction.atomic
def update_accommodation(public_id: UUID, **fields: Any) -> AccommodationDTO:
    return to_accommodation_dto(_update(_get(Accommodation, public_id), fields))


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@transaction.atomic
def create_tag(**fields: Any) -> TagDTO:
    """The §24.7 vocabulary. Rows, so a new interest needs no deployment."""
    return to_tag_dto(_create(Tag, fields))


@transaction.atomic
def update_tag(public_id: UUID, **fields: Any) -> TagDTO:
    return to_tag_dto(_update(_get(Tag, public_id), fields))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def set_active(model: type[SoftDeleteModel], public_id: UUID, *, active: bool) -> None:
    """§4.1's activate/deactivate, for any curated entity.

    Deactivating a parent hides everything beneath it, because
    `selectors.visibility_q` walks the chain. That is the whole mechanism
    behind Pemba: one flag on one destination, and its attractions leave the
    listings, the detail URLs and the sitemap together.
    """
    # Through `_update` rather than a direct assignment, so the writable-field
    # guard applies here too: a model whose `_WRITABLE` set omits `is_active`
    # is a model this must refuse to toggle, not one it quietly toggles anyway.
    _update(_get(model, public_id), {"is_active": active})


@transaction.atomic
def soft_delete(model: type[SoftDeleteModel], public_id: UUID) -> None:
    """§7.7. The row stays for referential integrity; the slug is released."""
    _get(model, public_id).delete()


@transaction.atomic
def restore(model: type[SoftDeleteModel], public_id: UUID) -> None:
    """Undo a soft deletion.

    Can fail on the partial unique index if the slug was reused in the
    meantime, and that failure is correct: two live rows may not share a slug,
    and silently renaming one would break whichever URL was already published.
    """
    _get(model, public_id).restore()
