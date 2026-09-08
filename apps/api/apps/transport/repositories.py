"""Data-access layer (SRS §8.2 layer 4).

Reads and writes for the tariff tables. No business rules: §12.4's ladder is
`domain.tariffs` and it is pure, so everything here is a `WHERE` clause.

**Everything is batched.** A quote covers several legs and each leg is priced
for every vehicle class that fits the party, so a per-leg-per-class query would
be a nested N+1 on the hottest path in the module. The two readers below take
the whole set of endpoints at once and return every row that could possibly
answer; the ladder does the choosing in memory, where it is a pure function
over a list and can be tested as one.

**The window filter is deliberately loose.** These return rows the ladder then
re-filters with `applies_on`. That looks redundant and is not: the domain must
be correct when handed anything, because a caller that assembles rules from a
seed file or a preview form never comes through here at all. Two checks that
agree cost one comparison; one check that is skipped costs a wrong price.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from django.db.models import Q, QuerySet

from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass

__all__ = [
    "live_vehicle_classes",
    "corridors_between",
    "tariffs_for",
    "published_corridors",
    "create_corridor",
    "update_corridor",
    "create_tariff",
    "update_tariff",
    "vehicle_class_by_code",
]

#: The columns an administrator may write. Anything not here — `public_id`,
#: `created_at`, `deleted_at`, `id` — is the platform's, and a payload naming
#: one is rejected rather than ignored, in the shape `catalogue.repositories`
#: already established.
_CORRIDOR_WRITABLE = frozenset(
    {
        "origin_destination_id",
        "target_destination_id",
        "vehicle_class",
        "fixed_price",
        "currency",
        "is_bidirectional",
        "valid_from",
        "valid_to",
        "is_active",
    }
)

_TARIFF_WRITABLE = frozenset(
    {
        "scope",
        "region_id",
        "country_id",
        "vehicle_class",
        "base_fare",
        "per_km_rate",
        "per_minute_rate",
        "minimum_fare",
        "night_surcharge_pct",
        "night_from",
        "night_to",
        "airport_surcharge",
        "waiting_rate_per_minute",
        "currency",
        "valid_from",
        "valid_to",
        "is_active",
    }
)


def _reject_unwritable(fields: dict[str, Any], allowed: frozenset[str], what: str) -> None:
    extra = set(fields) - allowed
    if extra:
        raise ValueError(f"{what} cannot be written through this path: {sorted(extra)}")


def live_vehicle_classes() -> QuerySet[VehicleClass]:
    """Every class a leg may be quoted for, in §24.16's card order."""
    return VehicleClass.objects.filter(is_active=True).order_by("display_order", "code")


def vehicle_class_by_code(code: str) -> VehicleClass | None:
    return VehicleClass.objects.filter(code=code, is_active=True).first()


def corridors_between(
    pairs: Iterable[tuple[int, int]], *, class_ids: Sequence[int]
) -> list[TransferCorridor]:
    """Every corridor that could answer for any of these ordered pairs.

    Both directions are fetched in one go, because step 2 of the ladder reads a
    bidirectional corridor backwards and a query per direction would double the
    round trips for no gain. Which of the two matched is the ladder's business,
    and it records the answer as `step`.
    """
    wanted = Q()
    seen: set[tuple[int, int]] = set()
    for origin, target in pairs:
        for a, b in ((origin, target), (target, origin)):
            if (a, b) in seen:
                continue
            seen.add((a, b))
            wanted |= Q(origin_destination_id=a, target_destination_id=b)

    if not seen or not class_ids:
        return []

    return list(
        TransferCorridor.objects.filter(wanted, vehicle_class_id__in=class_ids, is_active=True)
        .select_related("vehicle_class")
        .order_by("id")
    )


def tariffs_for(
    *, region_ids: Sequence[int], country_ids: Sequence[int], class_ids: Sequence[int]
) -> list[TransferTariff]:
    """Every metered rule that could answer on step 3 or step 4.

    Both scopes in one query. Step 4 is a fallback rather than a replacement —
    §12.4 expects both to be configured — so fetching only the regional rows
    and coming back for the national ones would be two queries where the second
    is needed most of the time anyway.
    """
    if not class_ids:
        return []

    scoped = Q()
    if region_ids:
        scoped |= Q(scope=TariffScope.REGION, region_id__in=region_ids)
    if country_ids:
        scoped |= Q(scope=TariffScope.COUNTRY, country_id__in=country_ids)
    if not scoped:
        return []

    return list(
        TransferTariff.objects.filter(scoped, vehicle_class_id__in=class_ids, is_active=True)
        .select_related("vehicle_class")
        .order_by("id")
    )


def published_corridors() -> QuerySet[TransferCorridor]:
    """§9.3.4's public corridor list. Routes, not prices — see `dto.CorridorDTO`."""
    return (
        TransferCorridor.objects.filter(is_active=True)
        .select_related("vehicle_class")
        .order_by("origin_destination_id", "target_destination_id", "vehicle_class__code")
    )


def create_corridor(**fields: Any) -> TransferCorridor:
    _reject_unwritable(fields, _CORRIDOR_WRITABLE, "corridor field")
    row: TransferCorridor = TransferCorridor.objects.create(**fields)
    return row


def update_corridor(row: TransferCorridor, **fields: Any) -> TransferCorridor:
    _reject_unwritable(fields, _CORRIDOR_WRITABLE, "corridor field")
    for name, value in fields.items():
        setattr(row, name, value)
    row.save(update_fields=[*fields, "updated_at"])
    return row


def create_tariff(**fields: Any) -> TransferTariff:
    _reject_unwritable(fields, _TARIFF_WRITABLE, "tariff field")
    row: TransferTariff = TransferTariff.objects.create(**fields)
    return row


def update_tariff(row: TransferTariff, **fields: Any) -> TransferTariff:
    _reject_unwritable(fields, _TARIFF_WRITABLE, "tariff field")
    for name, value in fields.items():
        setattr(row, name, value)
    row.save(update_fields=[*fields, "updated_at"])
    return row
