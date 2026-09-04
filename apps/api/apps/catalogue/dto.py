"""catalogue module — SRS §6.4.

Data transfer objects.

    Importable across module boundaries alongside services (SRS §6.5
    rule 1). Plain frozen dataclasses — no ORM, no Django.

SRS §6.5 rule 5: *"every module's services.py exposes only DTOs and primitives
— never ORM instances — across module boundaries."* These are that boundary,
and `tests/test_architecture.py` asserts it.

Three properties are carried deliberately rather than incidentally.

**No `id`, anywhere.** §7.2: *"Sequential integers are never returned to
clients."* Every DTO identifies itself by `public_id`. `trip` will hold a
reference to a catalogue row, and it will hold the UUID.

**Coordinates are `domain.geo.Coordinates`**, not a pair of floats. §13.1
forbids planar approximations, and the value object refuses an out-of-range or
over-precise coordinate at construction, so a bad seed row fails at the
boundary rather than becoming a transfer quote.

**An accommodation DTO carries no price and no availability** — ADR 0013. It is
a location record, and the DTO shape is where that stops being a claim in a
document and becomes something a serializer cannot get wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from uuid import UUID

from apps.common.geo import Coordinates

__all__ = [
    "MediaDTO",
    "TagDTO",
    "CancellationPolicyDTO",
    "CountryDTO",
    "MarketRefDTO",
    "ListingRefDTO",
    "MarketDTO",
    "RegionDTO",
    "DestinationDTO",
    "AttractionDTO",
    "ActivityDTO",
    "ActivityScheduleDTO",
    "AccommodationDTO",
    "SearchHitDTO",
]


@dataclass(frozen=True, slots=True)
class MediaDTO:
    """§7.3 `media`, already ordered by `domain.media.order_media`.

    Identified by `file_key`, not by a `public_id`: §35.7 makes the key
    content-hashed, so it already is the identity, and `media` is the one
    catalogue table that is not a `BaseModel` because there is nothing to
    identify separately from its bytes.

    `width` and `height` are not optional in the DTO even though the column
    allows null: `next/image` needs them to reserve space before the image
    loads, and a missing pair is a §24 CLS budget failure. A row without them
    is filtered out rather than published, in `selectors.to_media_dto`.

    `attribution`, `license_code` and `license_url` travel with the image
    rather than being looked up beside it. A credit that lives anywhere other
    than the payload the picture arrives in is a credit that gets separated
    from it by the first cache, merge or re-render — and CC BY does not care
    that the omission was structural.
    """

    file_key: str
    alt_text: str
    width: int
    height: int
    is_primary: bool
    sort_order: int
    attribution: str
    license_code: str
    license_url: str
    source_url: str


@dataclass(frozen=True, slots=True)
class TagDTO:
    """The §24.7 chip vocabulary. Data, never a branch in code.

    Carries `public_id` because a tag is an administered row and §27.8's
    console edits it by identifier: a slug is what the chip is called and is
    free to change, which makes it the wrong thing to address the row by.
    """

    public_id: UUID
    slug: str
    label: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class CancellationPolicyDTO:
    """§14.6, as rows rather than as four names in code.

    `tiers` travels as the ordered list it is stored as — most generous first,
    `{hours_before, refund_percent}` — because a client showing a tourist what
    they get back needs the whole ladder, not the code. BR-106 is why the
    booking snapshots it: this DTO describes the policy *now*, and a booking
    confirmed last month was sold against whatever it said then.
    """

    public_id: UUID
    code: str
    name: str
    description: str
    tiers: tuple[Mapping[str, int], ...]


@dataclass(frozen=True, slots=True)
class CountryDTO:
    public_id: UUID
    iso_code: str
    name: str
    default_currency: str
    default_timezone: str


@dataclass(frozen=True, slots=True)
class MarketRefDTO:
    """A market named, not described. ADR 0018.

    Everything below a market needs to *name* it — the breadcrumb, and the
    `/[market]/…` path segment every catalogue URL now carries. Almost none of
    it needs to know whether the market is open, and `is_open` is a function
    of the clock: putting it here would force a `today` through
    `to_region_dto`, and from there through `create_region` and
    `update_region`, which would have the admin write path reading the clock
    to answer a question nobody asked it.

    So identity travels freely and openness does not travel at all. `MarketDTO`
    is the answer to "what markets are there", and it is the only one that
    takes a date.
    """

    public_id: UUID
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class ListingRefDTO:
    """A catalogue row named, for a module that holds its id — ADR 0012.

    ADR 0012 has another module store a reference as a plain integer and read
    the row back through "a service call returning a DTO". This is that DTO,
    and `services.resolve_refs` is that call.

    Deliberately the smallest useful shape. `trip` needs to render "Stone Town"
    and link to `/destinations/stone-town`; it does not need opening hours, a
    price or a gallery, and handing it those would couple an itinerary's
    rendering to fields catalogue is free to change. `MarketRefDTO` makes the
    same argument one tier up and keeps the same three fields.

    `timezone` is the fourth, and it earns its place because **every instant
    rendered against this row has to be rendered in its zone** (§13.1, §15.2).
    An itinerary is a list of times; a client that formatted them in the
    browser's zone would undo, at the last step, the work the server does to
    resolve every day boundary in the destination's. Identity and the zone
    travel together because a name without one cannot be used to display
    anything.
    """

    public_id: UUID
    slug: str
    name: str
    timezone: str


@dataclass(frozen=True, slots=True)
class MarketDTO:
    """The destination selector's row — §4.2 as amended to v1.5, §24.6.

    `is_open` is carried rather than left to the client, and it is not the
    same question as "did this row come back". The selector asks for *listed*
    markets, which includes ones whose catalogue is closed, so a consumer that
    inferred openness from presence would link a tile into a catalogue that
    404s. The rule stays server-side; the client renders it.
    """

    public_id: UUID
    name: str
    slug: str
    summary: str | None
    is_open: bool
    country: CountryDTO
    #: What the place looks like. §24.6's hero reads this, which is what keeps
    #: "show Zanzibar's beaches" a row in a table rather than a branch in code.
    media: tuple[MediaDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class RegionDTO:
    public_id: UUID
    name: str
    slug: str
    country: CountryDTO
    market: MarketRefDTO


@dataclass(frozen=True, slots=True)
class DestinationDTO:
    """§7.5.6.

    `timezone` travels with every destination because §7.2 renders timestamps
    in the destination's zone and §15.2 evaluates opening hours in it. A client
    that has the destination has the zone, and never has to ask.
    """

    public_id: UUID
    name: str
    slug: str
    summary: str | None
    description: str
    centroid: Coordinates
    timezone: str
    default_currency: str
    is_gateway: bool
    gateway_type: str | None
    gateway_code: str | None
    launch_date: date | None
    feature_rank: int
    region: RegionDTO
    media: tuple[MediaDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class AttractionDTO:
    """§15.1.

    `entrance_fee` is `Decimal` and never travels without `fee_currency`
    (§7.2). Both are null together — a free attraction — and the pairing is a
    CHECK constraint, not a convention.
    """

    public_id: UUID
    name: str
    slug: str
    summary: str | None
    description: str
    coordinates: Coordinates
    opening_hours: dict[str, object]
    entrance_fee: Decimal | None
    fee_currency: str | None
    visit_minutes: int | None
    tags: tuple[str, ...]
    accessibility_notes: str
    feature_rank: int
    destination: DestinationDTO
    media: tuple[MediaDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityDTO:
    """§16.1.

    `price_per_person` and `price_per_group` are `Decimal` paired with
    `currency` (§18.5). No converted figure appears here: §18.4 puts conversion
    at quote time, and a display conversion is an
    `apps.common.display_money.IndicativeAmount`, which the serializer applies
    over this DTO rather than the DTO carrying it.
    """

    public_id: UUID
    name: str
    slug: str
    summary: str | None
    description: str
    coordinates: Coordinates
    meeting_point: str
    duration_minutes: int
    price_per_person: Decimal
    price_per_group: Decimal | None
    currency: str
    min_pax: int
    max_pax: int
    min_age: int | None
    requirements: dict[str, object]
    inclusions: list[object]
    exclusions: list[object]
    booking_cutoff_hours: int
    confirmation_mode: str
    tags: tuple[str, ...]
    rating_avg: Decimal | None
    """BR-127: `None` below `review.min_display_count`, where a client shows
    "New". Never the raw mean when there are too few reviews to state one —
    see ADR 0015 for why the rule is applied here and not in the client."""

    rating_count: int
    feature_rank: int
    destination: DestinationDTO
    media: tuple[MediaDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityScheduleDTO:
    """SRS §16.2's recurring rule, as the console edits it.

    **`days` and not `weekday_mask`.** The column is a bitmask because
    `domain.schedules` does arithmetic with it; a person deciding whether the
    Mnemba boat runs on Sunday should not have to. The two are the same fact in
    two shapes, so only one of them crosses the boundary, and it is the one a
    reviewer can check against a provider's own timetable.

    **`capacity` is what future materialisation will produce, not what any
    departure holds.** §16.2 keeps the rule and the sellable instant apart for
    exactly this reason: lowering it closes seats on departures not yet
    generated and cannot touch a departure somebody has already paid for.
    That is why BR-023 lives on the departure endpoint and not on this one —
    there is nothing here to oversell.
    """

    public_id: UUID
    activity: UUID
    days: tuple[str, ...]
    start_time: time
    capacity: int
    valid_from: date
    valid_to: date | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class AccommodationDTO:
    """§7.5.7 and §14, as amended in v1.2 — ADR 0013.

    **A location record.** Where the property is, and when its day starts and
    ends. There is no rate, no availability, no room type, no cancellation
    policy and no provider, because in v1 the Platform does not sell the room:
    a STAY itinerary item anchored to one of these carries location and dates
    and nothing else.

    `check_in_time` and `check_out_time` are local wall times in
    `destination.timezone`, which is the only zone they mean anything in, and
    they are nullable — a property that has not published them falls back to
    the Appendix B destination defaults rather than pretending to a precision
    it does not have.
    """

    public_id: UUID
    name: str
    slug: str
    summary: str | None
    description: str
    property_type: str
    coordinates: Coordinates
    address_line: str
    check_in_time: time | None
    check_out_time: time | None
    feature_rank: int
    destination: DestinationDTO
    media: tuple[MediaDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchHitDTO:
    """One row of `GET /search`, across all four searched tables.

    Deliberately thin. §24.7's result list shows a kind, a name and a link; a
    hit that carried the whole entity would make the endpoint the slowest one
    in the catalogue and would fan out four `select_related` trees to render a
    line of text.
    """

    kind: str
    public_id: UUID
    name: str
    slug: str
    destination_slug: str | None
    rank: Decimal
