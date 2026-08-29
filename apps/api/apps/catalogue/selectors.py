"""catalogue module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). Read queries; returns DTOs.

Two rules from the domain are compiled into SQL here, and the value of this
module is that they are compiled in exactly one place each.

**Visibility.** `domain.visibility` decides whether a row is public from four
facts — `is_active`, `launch_date`, `deleted_at`, and the same three on every
ancestor. `visibility_q` is that decision as a `Q`, because a row that is never
loaded cannot leak through a serializer, a sitemap entry or a JSON-LD block.
`tests/test_selectors_visibility.py` runs the pure function and this filter over
the whole truth table and asserts they agree; without that test there are two
implementations of one rule and no way to notice when they diverge.

Note what is *not* relied on: `SoftDeleteModel`'s default manager already
excludes deleted rows, and `visibility_q` restates `deleted_at IS NULL` anyway.
Depending on the manager would make the guarantee a property of which manager a
caller happened to use, and `all_objects` exists.

**Ranking.** `domain.ranking.order_terms` describes §16.5's ordering as a
sequence of `OrderTerm`s; `apply_order` turns that description into ORM
ordering. `tests/test_selectors_ranking_db.py` sorts a fixture with the domain's
`rank_key` and asserts PostgreSQL returns that exact sequence, so a drift in
this translation fails there rather than in TC-902 six weeks later.

Two details in that translation carry weight out of proportion to their size.
`NULLS LAST` is stated explicitly on every term that declares it, because
PostgreSQL's default for `DESC` is `NULLS FIRST` and the difference is an
unrated listing sorting top of the page. And a column a model does not have is
annotated as a **constant of the same shape** rather than dropped from the
ordering — `accommodation` has no price and no rating since ADR 0013, and
feeding the ordering a null keeps it identical to `rank_key(price=None)`
instead of quietly ranking accommodation by a different expression.

`today` is a parameter, never `date.today()`. §4.1's scheduled launch is
evaluated in the destination's zone, and the server's zone is not it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import (
    BooleanField,
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Model,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.functions import Cast

from apps.catalogue.domain.geo import Coordinates
from apps.catalogue.domain.media import MediaItem, order_media
from apps.catalogue.domain.ranking import (
    OrderTerm,
    SortOption,
    displayable_rating,
    order_terms,
)
from apps.catalogue.domain.search import (
    Hit,
    SearchKind,
    merge_ranked,
    normalise_query,
    to_websearch_query,
)
from apps.catalogue.domain.visibility import is_publicly_visible
from apps.catalogue.dto import (
    AccommodationDTO,
    ActivityDTO,
    AttractionDTO,
    CancellationPolicyDTO,
    CountryDTO,
    DestinationDTO,
    MarketDTO,
    MarketRefDTO,
    MediaDTO,
    RegionDTO,
    SearchHitDTO,
    TagDTO,
)
from apps.catalogue.models import (
    Accommodation,
    Activity,
    Attraction,
    CancellationPolicy,
    Destination,
    Market,
    Media,
    MediaOwnerType,
    Tag,
)
from apps.common.config import get_setting
from apps.common.pagination import Page, decode_cursor, encode_cursor

__all__ = [
    "to_cancellation_policy_dto",
    "to_market_ref_dto",
    "to_market_dto",
    "visibility_q",
    "visible",
    "listed_markets",
    "list_markets",
    "get_market",
    "OrderedTerm",
    "order_plan",
    "apply_order",
    "ordering_fingerprint",
    "keyset_after",
    "reference_q",
    "list_destinations",
    "get_destination",
    "list_attractions",
    "get_attraction",
    "list_activities",
    "get_activity",
    "list_accommodation",
    "get_accommodation",
    "list_tags",
    "search",
]

_M = TypeVar("_M", bound=Model)


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------

#: The ancestor chain each model must pass, as ORM lookup prefixes, outermost
#: last. `""` is the row itself. `destination` and `market` carry a
#: `launch_date`, which is why the flag travels with the level rather than
#: being assumed — ADR 0018 added the second one, and the chains below are the
#: only place that had to know.
_CHAINS: Mapping[type[Model], tuple[tuple[str, bool], ...]] = {
    Market: (("", True), ("country__", False)),
    Destination: (
        ("", True),
        ("region__", False),
        ("region__market__", True),
        ("region__country__", False),
    ),
    Attraction: (
        ("", False),
        ("destination__", True),
        ("destination__region__", False),
        ("destination__region__market__", True),
        ("destination__region__country__", False),
    ),
    Activity: (
        ("", False),
        ("destination__", True),
        ("destination__region__", False),
        ("destination__region__market__", True),
        ("destination__region__country__", False),
    ),
    Accommodation: (
        ("", False),
        ("destination__", True),
        ("destination__region__", False),
        ("destination__region__market__", True),
        ("destination__region__country__", False),
    ),
}


def visibility_q(model: type[Model], *, today: date) -> Q:
    """`domain.visibility.visible_chain`, compiled for `model`.

    Every level contributes the same three conditions, and the destination
    level contributes a fourth. They are combined with AND, which is what
    `visible_chain`'s `all(...)` means, so a single inactive ancestor hides
    everything beneath it — the property that stops deactivating Pemba leaving
    its attractions reachable by direct URL.
    """
    try:
        chain = _CHAINS[model]
    except KeyError as exc:  # pragma: no cover - guarded by its own test
        raise LookupError(
            f"{model.__name__} has no declared visibility chain; add one rather "
            "than filtering ad hoc at the call site"
        ) from exc

    predicate = Q()
    for prefix, has_launch_date in chain:
        predicate &= Q(**{f"{prefix}deleted_at__isnull": True})
        predicate &= Q(**{f"{prefix}is_active": True})
        if has_launch_date:
            # A launch date of exactly today **is** visible: §4.1 calls it a
            # launch date, and a market that launches on the 12th is open on
            # the 12th.
            predicate &= Q(**{f"{prefix}launch_date__isnull": True}) | Q(
                **{f"{prefix}launch_date__lte": today}
            )
    return predicate


def visible(queryset: QuerySet[_M], *, today: date) -> QuerySet[_M]:
    """Restrict `queryset` to what the public may see."""
    return queryset.filter(visibility_q(queryset.model, today=today))


def listed_markets(*, today: date) -> QuerySet[Market]:
    """The destination selector's rows — ADR 0018, §24.6.

    **The one place in this module that does not use `visible`,** and the
    reason is the feature rather than an oversight. `visible(Market...)` would
    return only *open* markets, so an announced one would vanish from the
    landing page and the screen would say nothing about a place the Platform
    is about to serve. That reads as a broken selector, not as a guard.

    So this compiles `domain.visibility.is_listed` instead: active, not
    deleted, `launch_date` not consulted. It is a deliberate hole in the
    visibility guarantee, exactly one table wide and one column deep, and it
    leaks nothing — a listed market's regions, destinations and listings are
    still filtered by `visible`, which now has `market` in their chains.

    `today` is not used by the predicate. It is used by the annotation: every
    row comes back knowing whether it is `is_open`, because the caller renders
    a tile that either links into the catalogue or says the market is not open
    yet, and asking the caller to recompute that would put the rule in two
    places again.
    """
    # Bound before returning: `annotate` is typed as widening to
    # `QuerySet[Any]`, and returning it directly loses `Market` at the
    # boundary — where every caller then gets no checking at all.
    markets: QuerySet[Market] = (
        Market.objects.filter(deleted_at__isnull=True, is_active=True)
        .select_related("country")
        .annotate(
            is_open=ExpressionWrapper(
                Q(launch_date__isnull=True) | Q(launch_date__lte=today),
                output_field=BooleanField(),
            )
        )
        .order_by("name")
    )
    return markets


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

#: How each model supplies the fields §16.5 orders by. A string is a column; a
#: constant stands in for a column the model does not have, and it is a constant
#: rather than an omission so that the emitted ordering stays identical to
#: `rank_key` fed the same absence.
#:
#: `Cast` rather than a bare `Value`: PostgreSQL reads an integer literal in
#: `ORDER BY` as a column position and rejects every other literal outright
#: ("non-integer constant in ORDER BY"). `CAST(NULL AS numeric)` is an
#: expression, so it sorts, and the cast also pins the type the null is
#: compared as.
_NO_MONEY = Cast(Value(None), output_field=DecimalField(max_digits=14, decimal_places=2))
_NO_RATING = Cast(Value(None), output_field=DecimalField(max_digits=3, decimal_places=2))
_NO_COUNT = Cast(Value(0), output_field=IntegerField())
_NO_MINUTES = Cast(Value(None), output_field=IntegerField())


class _DisplayableRating:
    """Marker for a rating term that is gated by `review.min_display_count`.

    Not the raw `rating_avg` column. ADR 0017: a subject with too few published
    reviews to state a mean ranks as unrated, so ranking and display are the
    same rule and one five-star review cannot buy top placement while the page
    shows "New". Resolved into a `CASE` at plan time because the threshold is a
    setting and this table is a module constant.
    """


DISPLAYABLE_RATING = _DisplayableRating()

_RANK_SOURCES: Mapping[type[Model], Mapping[str, Any]] = {
    Activity: {
        "feature_rank": "feature_rank",
        "rating_avg": DISPLAYABLE_RATING,
        "rating_count": "rating_count",
        "price": "price_per_person",
        "duration_minutes": "duration_minutes",
    },
    Attraction: {
        "feature_rank": "feature_rank",
        "rating_avg": _NO_RATING,
        "rating_count": _NO_COUNT,
        "price": _NO_MONEY,
        "duration_minutes": "visit_minutes",
    },
    # ADR 0013: a location record has no rate and no reviews, so three of the
    # six terms are constants. The ordering is still the §16.5 expression —
    # feature_rank, then id — rather than a second, quietly different one.
    Accommodation: {
        "feature_rank": "feature_rank",
        "rating_avg": _NO_RATING,
        "rating_count": _NO_COUNT,
        "price": _NO_MONEY,
        "duration_minutes": _NO_MINUTES,
    },
    Destination: {
        "feature_rank": "feature_rank",
        "rating_avg": _NO_RATING,
        "rating_count": _NO_COUNT,
        "price": _NO_MONEY,
        "duration_minutes": _NO_MINUTES,
    },
}


@dataclass(frozen=True, slots=True)
class OrderedTerm:
    """One compiled §16.5 term: what to read, which way, where nulls go.

    The compiled form exists because two things need it and they must not
    disagree: `apply_order` turns it into `ORDER BY`, and `keyset_after` turns
    it into the `WHERE` that resumes inside that ordering. A cursor built from
    a different reading of the ordering than the one the query uses does not
    fail — it silently skips or repeats rows.
    """

    name: str
    """The queryset name: a column, or the annotation standing in for one."""

    descending: bool
    nulls_last: bool


def order_plan(
    model: type[Model],
    *,
    min_display_count: int,
    sort: SortOption = SortOption.DEFAULT,
    selected_destination_id: int | None = None,
    interest_tags: Collection[str] = (),
) -> tuple[dict[str, Any], tuple[OrderedTerm, ...]]:
    """§16.5 for this model, compiled once: the annotations, and the terms.

    The two context terms are annotations rather than columns: "is this row in
    the destination the tourist selected" and "does it carry any of their
    interest tags" are properties of the *request*, not of the row.
    """
    terms = order_terms(
        sort,
        selected_destination_id=selected_destination_id,
        interest_tags=interest_tags,
    )
    sources = _RANK_SOURCES[model]
    annotations: dict[str, Any] = {}
    plan: list[OrderedTerm] = []

    for term in terms:
        name, annotation = _resolve(
            term, sources, selected_destination_id, interest_tags, min_display_count
        )
        if annotation is not None:
            annotations[name] = annotation
        plan.append(OrderedTerm(name, term.descending, term.nulls_last))

    return annotations, tuple(plan)


def apply_order(
    queryset: QuerySet[_M],
    *,
    min_display_count: int | None = None,
    sort: SortOption = SortOption.DEFAULT,
    selected_destination_id: int | None = None,
    interest_tags: Collection[str] = (),
) -> QuerySet[_M]:
    """Order `queryset` by §16.5, as `domain.ranking` describes it."""
    if min_display_count is None:
        min_display_count = int(get_setting("review.min_display_count"))
    annotations, plan = order_plan(
        queryset.model,
        min_display_count=min_display_count,
        sort=sort,
        selected_destination_id=selected_destination_id,
        interest_tags=interest_tags,
    )
    if annotations:
        queryset = queryset.annotate(**annotations)
    return queryset.order_by(*(_direction(F(term.name), term) for term in plan))


def ordering_fingerprint(
    model: type[Model], plan: Sequence[OrderedTerm], *, min_display_count: int
) -> str:
    """A short digest of *which* ordering this is.

    Written into every cursor and checked on the way back. A cursor taken from
    a `?sort=price_asc` page and replayed against `?sort=recommended` names a
    position in an ordering that no longer exists; without this it would be
    honoured, and the rows it skipped would never be seen by that caller.

    The model is part of the material because two entities can compile to the
    same term names — `feature_rank, id` is the whole ordering for both a
    destination and an accommodation.

    `min_display_count` is part of it for the same reason the sort is. It does
    not change which terms there are, it changes what one of them *evaluates
    to* (ADR 0017): an administrator lowering the threshold mid-scroll gives
    previously-unrated subjects a rank, which moves rows across a page boundary
    that has already been issued. Honouring the old cursor would then skip
    them silently, which is the failure this digest exists to prevent.
    """
    material = ";".join(
        f"{model.__name__}.{term.name}:{int(term.descending)}{int(term.nulls_last)}"
        for term in plan
    )
    return hashlib.blake2s(
        f"{material}|min_display={min_display_count}".encode(), digest_size=8
    ).hexdigest()


def keyset_after(plan: Sequence[OrderedTerm], values: Sequence[object]) -> Q:
    """Everything strictly after `values` in the ordering `plan` describes.

    The lexicographic expansion, written out:

        (t1 after v1)
        OR (t1 = v1 AND t2 after v2)
        OR (t1 = v1 AND t2 = v2 AND t3 after v3) ...

    A row-value comparison — `(a, b, c) > (x, y, z)` — would be one clean
    predicate and cannot be used here: it requires every term to sort the same
    way, and §16.5 mixes ascending and descending and puts nulls last on three
    of them. The expansion is longer and is the only form that says what the
    `ORDER BY` says.

    The final term is `id`, which is unique and never null, so the last branch
    always excludes the cursor row itself. Without a unique final term this
    would loop on a page of ties.
    """
    branches: list[Q] = []
    equals = Q()
    for term, value in zip(plan, values, strict=True):
        after = _strictly_after(term, value)
        if after is not None:
            branches.append(equals & after)
        equals &= _equal(term, value)

    if not branches:
        # Every term of the cursor was null, and nulls sort last: there is
        # nothing after this row. An empty `Q()` would mean "everything".
        return Q(pk__in=[])

    combined = branches[0]
    for branch in branches[1:]:
        combined |= branch
    return combined


def _strictly_after(term: OrderedTerm, value: object) -> Q | None:
    """The rows this term places after `value`, or `None` for "none of them".

    Nulls sort last on every term that admits them, so nothing follows a null
    and the branch is dropped rather than emitted as a predicate matching
    nothing — one fewer `OR` for the planner to look at.
    """
    if not term.nulls_last:
        # No term declares this today. Failing loudly beats paginating a
        # NULLS FIRST ordering with NULLS LAST arithmetic, which would drop
        # every null row from every page after the first.
        raise NotImplementedError(f"keyset pagination assumes NULLS LAST; {term.name} does not")
    if value is None:
        return None
    comparison = "lt" if term.descending else "gt"
    return Q(**{f"{term.name}__{comparison}": value}) | Q(**{f"{term.name}__isnull": True})


def _equal(term: OrderedTerm, value: object) -> Q:
    if value is None:
        return Q(**{f"{term.name}__isnull": True})
    return Q(**{term.name: value})


def _resolve(
    term: OrderTerm,
    sources: Mapping[str, Any],
    selected_destination_id: int | None,
    interest_tags: Collection[str],
    min_display_count: int,
) -> tuple[str, Any | None]:
    """The queryset name for a term, plus the annotation it needs, if any."""
    if term.expression == "matches_selected_destination":
        return "matches_selected_destination", ExpressionWrapper(
            Q(destination_id=selected_destination_id), output_field=BooleanField()
        )
    if term.expression == "matches_interest_tags":
        return "matches_interest_tags", ExpressionWrapper(
            Q(tags__overlap=list(interest_tags)), output_field=BooleanField()
        )
    if term.expression == "id":
        return "id", None

    source = sources[term.expression]
    if isinstance(source, _DisplayableRating):
        # `domain.ranking.displayable_rating`, as SQL. The two are asserted to
        # agree by `test_selectors_ranking_db.py`, which sorts a fixture with
        # the pure function and requires PostgreSQL to return that sequence.
        return "rank_rating_avg", Case(
            When(rating_count__gte=min_display_count, then=F("rating_avg")),
            default=Value(None),
            output_field=DecimalField(max_digits=3, decimal_places=2),
        )
    if isinstance(source, str):
        return source, None
    # A constant standing in for an absent column. It is annotated under the
    # term's own name so the ordering reads the same for every model.
    return f"rank_{term.expression}", source


def _direction(expression: Any, term: OrderedTerm) -> Any:
    """Apply direction and null placement.

    `nulls_last` is passed on every term that declares it rather than left to
    the backend. PostgreSQL defaults `DESC` to `NULLS FIRST`, so an unrated
    listing would sort above a five-star one — which is the exact failure
    §16.5's published-ordering commitment cannot afford.
    """
    if term.descending:
        return expression.desc(nulls_last=term.nulls_last)
    return expression.asc(nulls_last=term.nulls_last)


# ---------------------------------------------------------------------------
# Mapping to DTOs
# ---------------------------------------------------------------------------


def _coordinates(point: Any) -> Coordinates:
    """A PostGIS point as the domain's value object.

    `Decimal(str(...))` rather than `Decimal(float)`: the binary float would
    carry seventeen significant digits into a type that rejects more than
    `COORDINATE_PRECISION` decimal places.
    """
    return Coordinates(lat=Decimal(str(point.y)), lon=Decimal(str(point.x)))


def to_media_dto(media: Media) -> MediaDTO | None:
    """One `media` row, or `None` where it cannot be rendered safely.

    A row without both dimensions is dropped rather than published: `next/image`
    cannot reserve space without them, and the resulting layout shift is a §24
    Lighthouse CLS failure. Dropping one image is a smaller defect than failing
    the performance gate on every page that shows it.
    """
    if media.width is None or media.height is None:
        return None
    return MediaDTO(
        file_key=media.file_key,
        alt_text=media.alt_text,
        width=media.width,
        height=media.height,
        is_primary=media.is_primary,
        sort_order=media.sort_order,
    )


def _gallery(owner_type: MediaOwnerType, rows: Iterable[Media]) -> tuple[MediaDTO, ...]:
    """The ordered gallery for one owner, per `domain.media.order_media`."""
    items = [
        MediaItem(
            id=row.id,
            file_key=row.file_key,
            alt_text=row.alt_text,
            width=row.width,
            height=row.height,
            is_primary=row.is_primary,
            sort_order=row.sort_order,
        )
        for row in rows
        if row.owner_type == owner_type
    ]
    by_id = {row.id: row for row in rows}
    ordered = (to_media_dto(by_id[item.id]) for item in order_media(items))
    return tuple(dto for dto in ordered if dto is not None)


def to_country_dto(country: Any) -> CountryDTO:
    return CountryDTO(
        public_id=country.public_id,
        iso_code=country.iso_code,
        name=country.name,
        default_currency=country.default_currency,
        default_timezone=country.default_timezone,
    )


def to_market_ref_dto(market: Any) -> MarketRefDTO:
    return MarketRefDTO(public_id=market.public_id, name=market.name, slug=market.slug)


def to_market_dto(market: Any, *, today: date) -> MarketDTO:
    """`today` is required, not defaulted.

    `is_open` is a function of the clock, and a default would let a caller
    omit the one argument that decides whether a tile links into a catalogue
    or says the market is not open yet — the same reasoning that keeps `today`
    a parameter throughout this module and the domain.

    It reads `domain.visibility.is_publicly_visible` rather than restating the
    comparison. `listed_markets` annotates the same value in SQL for ordering
    and filtering; this is the one that reaches a client, and the two must not
    be two rules.
    """
    return MarketDTO(
        public_id=market.public_id,
        name=market.name,
        slug=market.slug,
        summary=market.summary,
        is_open=is_publicly_visible(
            is_active=market.is_active,
            deleted_at=market.deleted_at,
            launch_date=market.launch_date,
            today=today,
        ),
        country=to_country_dto(market.country),
    )


def to_region_dto(region: Any) -> RegionDTO:
    return RegionDTO(
        public_id=region.public_id,
        name=region.name,
        slug=region.slug,
        country=to_country_dto(region.country),
        market=to_market_ref_dto(region.market),
    )


def to_destination_dto(destination: Destination, *, media: Sequence[Media] = ()) -> DestinationDTO:
    return DestinationDTO(
        public_id=destination.public_id,
        name=destination.name,
        slug=destination.slug,
        summary=destination.summary,
        description=destination.description,
        centroid=_coordinates(destination.centroid),
        timezone=destination.timezone,
        default_currency=destination.default_currency,
        is_gateway=destination.is_gateway,
        gateway_type=destination.gateway_type or None,
        gateway_code=destination.gateway_code or None,
        launch_date=destination.launch_date,
        feature_rank=destination.feature_rank,
        region=to_region_dto(destination.region),
        media=_gallery(MediaOwnerType.DESTINATION, media),
    )


def to_attraction_dto(attraction: Attraction, *, media: Sequence[Media] = ()) -> AttractionDTO:
    return AttractionDTO(
        public_id=attraction.public_id,
        name=attraction.name,
        slug=attraction.slug,
        summary=attraction.summary,
        description=attraction.description,
        coordinates=_coordinates(attraction.coordinates),
        opening_hours=attraction.opening_hours or {},
        entrance_fee=attraction.entrance_fee,
        fee_currency=attraction.fee_currency or None,
        visit_minutes=attraction.visit_minutes,
        tags=tuple(attraction.tags),
        accessibility_notes=attraction.accessibility_notes,
        feature_rank=attraction.feature_rank,
        destination=to_destination_dto(attraction.destination),
        media=_gallery(MediaOwnerType.ATTRACTION, media),
    )


def to_activity_dto(
    activity: Activity,
    *,
    media: Sequence[Media] = (),
    min_display_count: int | None = None,
) -> ActivityDTO:
    """One activity as the wire sees it, with BR-127 already applied.

    `min_display_count` is read here when it is not supplied, and supplied by
    the list path so that a page of fifty rows performs one settings read
    rather than fifty. The rule itself lives in `domain.ranking`; what happens
    here is only that the DTO carries the suppressed value, so no serializer
    and no client is ever handed a mean it may not display.
    """
    if min_display_count is None:
        min_display_count = int(get_setting("review.min_display_count"))
    return ActivityDTO(
        public_id=activity.public_id,
        name=activity.name,
        slug=activity.slug,
        summary=activity.summary,
        description=activity.description,
        coordinates=_coordinates(activity.coordinates),
        meeting_point=activity.meeting_point_text,
        duration_minutes=activity.duration_minutes,
        price_per_person=activity.price_per_person,
        price_per_group=activity.price_per_group,
        currency=activity.currency,
        min_pax=activity.min_pax,
        max_pax=activity.max_pax,
        min_age=activity.min_age,
        requirements=activity.requirements or {},
        inclusions=list(activity.inclusions or []),
        exclusions=list(activity.exclusions or []),
        booking_cutoff_hours=activity.booking_cutoff_hours,
        confirmation_mode=activity.confirmation_mode,
        tags=tuple(activity.tags),
        rating_avg=displayable_rating(
            activity.rating_avg, activity.rating_count, min_display_count=min_display_count
        ),
        rating_count=activity.rating_count,
        feature_rank=activity.feature_rank,
        destination=to_destination_dto(activity.destination),
        media=_gallery(MediaOwnerType.ACTIVITY, media),
    )


def to_accommodation_dto(
    accommodation: Accommodation, *, media: Sequence[Media] = ()
) -> AccommodationDTO:
    """ADR 0013: everything the DTO can carry, and nothing it cannot.

    There is no branch here suppressing a rate or an availability figure,
    because there is no column to suppress. That is the difference between
    deferring a subsystem and hiding one.
    """
    return AccommodationDTO(
        public_id=accommodation.public_id,
        name=accommodation.name,
        slug=accommodation.slug,
        summary=accommodation.summary,
        description=accommodation.description,
        property_type=accommodation.property_type,
        coordinates=_coordinates(accommodation.coordinates),
        address_line=accommodation.address_line,
        check_in_time=accommodation.check_in_time,
        check_out_time=accommodation.check_out_time,
        feature_rank=accommodation.feature_rank,
        destination=to_destination_dto(accommodation.destination),
        media=_gallery(MediaOwnerType.ACCOMMODATION, media),
    )


def to_cancellation_policy_dto(policy: CancellationPolicy) -> CancellationPolicyDTO:
    return CancellationPolicyDTO(
        public_id=policy.public_id,
        code=policy.code,
        name=policy.name,
        description=policy.description,
        tiers=tuple(policy.tiers),
    )


def to_tag_dto(tag: Tag) -> TagDTO:
    return TagDTO(
        public_id=tag.public_id, slug=tag.slug, label=tag.label, sort_order=tag.sort_order
    )


# ---------------------------------------------------------------------------
# Read queries
# ---------------------------------------------------------------------------

#: Every list query eager-loads the whole ancestor chain. §29's NFR-P01 budget
#: does not survive one query per row for the destination, and the chain is
#: needed anyway because `visibility_q` already joins it.
#: `market` is here as well as in `_CHAINS`, and it has to be: a level that
#: the visibility filter joins but `select_related` does not is a lazy load per
#: row, which is how a two-query page became a twenty-seven-query one the
#: moment ADR 0018 landed. `test_a_page_is_a_constant_number_of_queries` is
#: what said so.
_DESTINATION_TREE = ("region", "region__market", "region__country")
_LISTING_TREE = (
    "destination",
    "destination__region",
    "destination__region__market",
    "destination__region__country",
)


def _media_for(owner_type: MediaOwnerType, owner_ids: Sequence[int]) -> list[Media]:
    """One query for every gallery on the page, not one per row."""
    if not owner_ids:
        return []
    return list(Media.objects.filter(owner_type=owner_type, owner_id__in=list(owner_ids)))


def _by_owner(rows: Sequence[Media]) -> dict[int, list[Media]]:
    grouped: dict[int, list[Media]] = {}
    for row in rows:
        grouped.setdefault(row.owner_id, []).append(row)
    return grouped


def reference_q(reference: str | UUID) -> Q:
    """Address a public row by `public_id` or by slug.

    §7.2 makes the UUID the identifier the API exchanges, and §24.8 serves its
    pages from slugs — `/destinations/zanzibar` is what a person reads, links
    and shares, and a UUID in that position would be a worse URL for no gain.
    Both resolve to the same row.

    Resolution is here rather than in the view because "which of these two is
    it" is a question about the data model, and because a view that guessed
    would be a second place to guess differently.
    """
    if isinstance(reference, UUID):
        return Q(public_id=reference)
    try:
        return Q(public_id=UUID(reference))
    except ValueError:
        return Q(slug=reference)


def _paged(
    queryset: QuerySet[_M],
    *,
    limit: int,
    cursor: str | None,
    sort: SortOption = SortOption.DEFAULT,
    selected_destination_id: int | None = None,
    interest_tags: Collection[str] = (),
) -> tuple[list[_M], str | None]:
    """One page of `queryset` in §16.5 order, and the cursor for the next.

    `limit + 1` rows are fetched and the extra one is discarded. That is what
    makes `next_cursor` truthful without a second `COUNT(*)` over a filtered,
    ordered, joined query — and a `next_cursor` present on the last page is a
    client that renders one empty final screen.

    The ordering is compiled once and used twice: for the `ORDER BY`, and for
    the cursor's contents. They cannot drift, because they are the same object.
    """
    min_display_count = int(get_setting("review.min_display_count"))
    annotations, plan = order_plan(
        queryset.model,
        min_display_count=min_display_count,
        sort=sort,
        selected_destination_id=selected_destination_id,
        interest_tags=interest_tags,
    )
    fingerprint = ordering_fingerprint(queryset.model, plan, min_display_count=min_display_count)
    if annotations:
        queryset = queryset.annotate(**annotations)
    if cursor:
        queryset = queryset.filter(keyset_after(plan, decode_cursor(cursor, ordering=fingerprint)))
    queryset = queryset.order_by(*(_direction(F(term.name), term) for term in plan))

    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    if not (has_more and rows):
        return rows, None
    position = [getattr(rows[-1], term.name) for term in plan]
    return rows, encode_cursor(position, ordering=fingerprint)


def list_destinations(
    *,
    today: date,
    region_slug: str | None = None,
    is_gateway: bool | None = None,
    limit: int = 20,
    cursor: str | None = None,
) -> Page[DestinationDTO]:
    queryset = visible(Destination.objects.select_related(*_DESTINATION_TREE), today=today)
    if region_slug is not None:
        queryset = queryset.filter(region__slug=region_slug)
    if is_gateway is not None:
        queryset = queryset.filter(is_gateway=is_gateway)
    rows, next_cursor = _paged(queryset, limit=limit, cursor=cursor)
    galleries = _by_owner(_media_for(MediaOwnerType.DESTINATION, [row.id for row in rows]))
    return Page(
        tuple(to_destination_dto(row, media=galleries.get(row.id, [])) for row in rows),
        next_cursor,
    )


def list_markets(*, today: date) -> tuple[MarketDTO, ...]:
    """Every listed market — the destination selector's whole payload. §24.6.

    **Not paginated, and that is a decision rather than an omission.** Two
    reasons. The §16.5 ordering `_paged` compiles is built from price, rating
    and feature rank, none of which a market has, so paginating this would
    mean either inventing those columns or a second ordering to keep in step.
    And the screen needs all of them at once: a selector that arrived a page at
    a time would make the landing page's first paint depend on a cursor walk.

    The usual objection to an unbounded public list does not apply here.
    `market` has no public write path — it is administered through §27.8 —
    so its size is a business decision, not something a caller can inflate.
    That is the opposite of `/search`, which is why that one is bounded.

    Ordered by name: the tile grid has no ranking signal to sort on, and a
    stable alphabetical order is one a returning visitor recognises.
    """
    return tuple(to_market_dto(row, today=today) for row in listed_markets(today=today))


def get_market(*, reference: str | UUID, today: date) -> MarketDTO | None:
    """One market, **listed rather than open**.

    The deliberate difference from `get_destination`. That one hides a row
    that exists but has not launched, because a distinguishable "exists but
    hidden" would publish an unopened market's launch date. Here the launch is
    the thing being published: §24.6's announcement page is a market that is
    listed and not open, and returning 404 for it would make the tile on the
    landing page lead nowhere.

    So this uses `is_listed`, and the caller reads `is_open` to decide between
    an announcement and a catalogue. Nothing beneath the market is affected —
    its regions, destinations and listings are still filtered by `visible`,
    which has `market` in their chains.
    """
    row = listed_markets(today=today).filter(reference_q(reference)).first()
    return None if row is None else to_market_dto(row, today=today)


def get_destination(*, reference: str | UUID, today: date) -> DestinationDTO | None:
    """`None` where the row does not exist **or is not public**.

    The two are deliberately indistinguishable to a caller, for the same reason
    §30.3 returns 404 rather than 403: a distinguishable "exists but hidden"
    publishes the launch date of a market that has not opened.
    """
    row = (
        visible(Destination.objects.select_related(*_DESTINATION_TREE), today=today)
        .filter(reference_q(reference))
        .first()
    )
    if row is None:
        return None
    return to_destination_dto(row, media=_media_for(MediaOwnerType.DESTINATION, [row.id]))


def list_attractions(
    *,
    today: date,
    destination_slug: str | None = None,
    tags: Collection[str] = (),
    sort: SortOption = SortOption.DEFAULT,
    limit: int = 20,
    cursor: str | None = None,
) -> Page[AttractionDTO]:
    queryset = visible(Attraction.objects.select_related(*_LISTING_TREE), today=today)
    selected = _destination_id(destination_slug, today=today) if destination_slug else None
    if destination_slug is not None:
        queryset = queryset.filter(destination__slug=destination_slug)
    if tags:
        queryset = queryset.filter(tags__overlap=list(tags))
    rows, next_cursor = _paged(
        queryset,
        limit=limit,
        cursor=cursor,
        sort=sort,
        selected_destination_id=selected,
        interest_tags=tags,
    )
    galleries = _by_owner(_media_for(MediaOwnerType.ATTRACTION, [row.id for row in rows]))
    return Page(
        tuple(to_attraction_dto(row, media=galleries.get(row.id, [])) for row in rows),
        next_cursor,
    )


def get_attraction(*, reference: str | UUID, today: date) -> AttractionDTO | None:
    row = (
        visible(Attraction.objects.select_related(*_LISTING_TREE), today=today)
        .filter(reference_q(reference))
        .first()
    )
    if row is None:
        return None
    return to_attraction_dto(row, media=_media_for(MediaOwnerType.ATTRACTION, [row.id]))


def list_activities(
    *,
    today: date,
    destination_slug: str | None = None,
    tags: Collection[str] = (),
    sort: SortOption = SortOption.DEFAULT,
    limit: int = 20,
    cursor: str | None = None,
) -> Page[ActivityDTO]:
    queryset = visible(Activity.objects.select_related(*_LISTING_TREE), today=today)
    selected = _destination_id(destination_slug, today=today) if destination_slug else None
    if destination_slug is not None:
        queryset = queryset.filter(destination__slug=destination_slug)
    if tags:
        queryset = queryset.filter(tags__overlap=list(tags))
    rows, next_cursor = _paged(
        queryset,
        limit=limit,
        cursor=cursor,
        sort=sort,
        selected_destination_id=selected,
        interest_tags=tags,
    )
    galleries = _by_owner(_media_for(MediaOwnerType.ACTIVITY, [row.id for row in rows]))
    # Read once for the page, not once per row: BR-127's threshold is the same
    # for every activity in it, and a settings read per row becomes an N+1 the
    # day the database-backed provider replaces the in-memory defaults.
    min_display_count = int(get_setting("review.min_display_count"))
    return Page(
        tuple(
            to_activity_dto(
                row, media=galleries.get(row.id, []), min_display_count=min_display_count
            )
            for row in rows
        ),
        next_cursor,
    )


def get_activity(*, reference: str | UUID, today: date) -> ActivityDTO | None:
    row = (
        visible(Activity.objects.select_related(*_LISTING_TREE), today=today)
        .filter(reference_q(reference))
        .first()
    )
    if row is None:
        return None
    return to_activity_dto(row, media=_media_for(MediaOwnerType.ACTIVITY, [row.id]))


def list_accommodation(
    *,
    today: date,
    destination_slug: str | None = None,
    property_types: Collection[str] = (),
    limit: int = 20,
    cursor: str | None = None,
) -> Page[AccommodationDTO]:
    """The curated location list §24.11 offers before free entry.

    No dates, no occupancy, no availability and no sort-by-price parameter:
    there is nothing here to filter on price or to be available. The tourist is
    naming where they are already staying, not shopping.
    """
    queryset = visible(Accommodation.objects.select_related(*_LISTING_TREE), today=today)
    selected = _destination_id(destination_slug, today=today) if destination_slug else None
    if destination_slug is not None:
        queryset = queryset.filter(destination__slug=destination_slug)
    if property_types:
        queryset = queryset.filter(property_type__in=list(property_types))
    rows, next_cursor = _paged(
        queryset, limit=limit, cursor=cursor, selected_destination_id=selected
    )
    galleries = _by_owner(_media_for(MediaOwnerType.ACCOMMODATION, [row.id for row in rows]))
    return Page(
        tuple(to_accommodation_dto(row, media=galleries.get(row.id, [])) for row in rows),
        next_cursor,
    )


def get_accommodation(*, reference: str | UUID, today: date) -> AccommodationDTO | None:
    row = (
        visible(Accommodation.objects.select_related(*_LISTING_TREE), today=today)
        .filter(reference_q(reference))
        .first()
    )
    if row is None:
        return None
    return to_accommodation_dto(row, media=_media_for(MediaOwnerType.ACCOMMODATION, [row.id]))


def list_tags() -> tuple[TagDTO, ...]:
    """§24.7's chip vocabulary. No visibility chain: a tag has no parent."""
    rows = Tag.objects.filter(deleted_at__isnull=True, is_active=True).order_by(
        "sort_order", "slug"
    )
    return tuple(to_tag_dto(row) for row in rows)


def _destination_id(slug: str, *, today: date) -> int | None:
    """The private key for a slug, for the §16.5 context term only.

    Never returned to a caller — §7.2, sequential integers do not leave the
    database. It is looked up through `visible` so that filtering by a hidden
    destination cannot promote its listings in the ordering.
    """
    found: int | None = (
        visible(Destination.objects.all(), today=today)
        .filter(slug=slug)
        .values_list("id", flat=True)
        .first()
    )
    return found


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

#: Which model backs each searched kind, and how to read its destination slug.
_SEARCHED: Mapping[SearchKind, tuple[type[Model], str | None]] = {
    SearchKind.DESTINATION: (Destination, None),
    SearchKind.ATTRACTION: (Attraction, "destination__slug"),
    SearchKind.ACTIVITY: (Activity, "destination__slug"),
    SearchKind.ACCOMMODATION: (Accommodation, "destination__slug"),
}


def search(
    raw: str,
    *,
    today: date,
    min_length: int,
    max_length: int,
    kinds: Collection[SearchKind] = (),
    limit_per_kind: int = 20,
) -> tuple[SearchHitDTO, ...]:
    """`GET /search` across the four searched tables. §7.6, §9.3.2, §24.7.

    The query goes through `domain.search` before it reaches the database:
    `websearch_to_tsquery` never raises on human input, where `to_tsquery`
    raises a database error on a stray `&` — a 500 on a public unauthenticated
    endpoint, and a cheap one to trigger.

    Ordering is `domain.search.merge_ranked`, not four `ORDER BY`s stapled
    together. Relevance alone ties constantly across tables, and a tie broken
    by whichever query returned first is a result list that reorders itself
    between two identical requests — which TC-902 forbids.
    """
    cleaned = normalise_query(raw, min_length=min_length, max_length=max_length)
    query = SearchQuery(to_websearch_query(cleaned), search_type="websearch", config="english")

    wanted = tuple(kinds) if kinds else tuple(_SEARCHED)
    groups: dict[SearchKind, list[Hit]] = {}
    rows_by_kind: dict[SearchKind, dict[int, Any]] = {}

    for kind in wanted:
        model, slug_path = _SEARCHED[kind]
        fields = ["id", "public_id", "name", "slug"]
        if slug_path is not None:
            fields.append(slug_path)
        found = list(
            visible(model._default_manager.all(), today=today)
            .filter(search_vector=query)
            .annotate(relevance=SearchRank(F("search_vector"), query))
            .values(*fields, "relevance")[:limit_per_kind]
        )
        groups[kind] = [
            # `SearchRank` returns a float; it becomes a `Decimal` here and
            # stays one, because §18.5's prohibition on float is about the
            # money path but the determinism argument is the same — a float
            # comparison that ties differently between two identical requests
            # is exactly what TC-902 forbids.
            Hit(kind=kind, id=row["id"], rank=Decimal(str(row["relevance"])))
            for row in found
        ]
        rows_by_kind[kind] = {row["id"]: row for row in found}

    ordered = merge_ranked(groups)
    return tuple(
        SearchHitDTO(
            kind=str(hit.kind),
            public_id=rows_by_kind[hit.kind][hit.id]["public_id"],
            name=rows_by_kind[hit.kind][hit.id]["name"],
            slug=rows_by_kind[hit.kind][hit.id]["slug"],
            destination_slug=rows_by_kind[hit.kind][hit.id].get("destination__slug"),
            rank=hit.rank,
        )
        for hit in ordered
    )
