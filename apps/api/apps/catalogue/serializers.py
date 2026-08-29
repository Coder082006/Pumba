"""catalogue module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). Syntactic validation only.

What these do is reject input that is the wrong *shape*. Every rule about what
the values may mean lives in `domain/`, reaches the row through
`apps.catalogue.validators` and `full_clean`, and is enforced again by a CHECK
constraint. A timezone is not validated here, because there would then be two
answers to "is `Africa/Zanzibar` a zone" and the console — which writes through
`full_clean` and never touches a serializer — would only see one of them.

Three shape decisions are worth stating.

**Coordinates cross the wire as `latitude`/`longitude`, never as GeoJSON or
WKT.** §13.1 fixes the exchange format: *"decimal degrees with a maximum of
seven decimal places"*. `DecimalField(decimal_places=7)` is that sentence, and
it refuses the eighth digit at the boundary rather than letting PostGIS store
noise. They are converted to a `Point` here and nowhere else.

**A reference crosses as the target's `public_id`.** §7.2: *"Sequential
integers are never returned to clients"*, and an identifier a client may not
read is one it may not write either. The UUID is resolved to a row in
`services`, which is the layer allowed to ask the database anything.

**Every write serializer is a `StrictSerializer` with an explicit field list.**
§30.6, twice over: an unknown key is a 422 naming the field, and there is no
path by which a key this file does not name reaches a column.

`_ACTIVITY_PROVIDER` is absent from `ActivityWriteSerializer` deliberately.
`activity.provider_id` is writable in the repository — the Phase 11 portal
needs it — but it is an internal integer, and exposing it on an admin endpoint
would put a sequential id on the wire to satisfy a caller that does not exist
yet.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.catalogue.domain.geo import COORDINATE_PRECISION
from apps.catalogue.domain.ranking import SortOption
from apps.catalogue.domain.search import SearchKind
from apps.catalogue.models import (
    ConfirmationMode,
    GatewayTypeChoices,
    PropertyType,
)
from apps.common.serializers import StrictSerializer

__all__ = [
    "CountryWriteSerializer",
    "MarketWriteSerializer",
    "RegionWriteSerializer",
    "DestinationWriteSerializer",
    "TagWriteSerializer",
    "CancellationPolicyTierSerializer",
    "CancellationPolicyWriteSerializer",
    "AttractionWriteSerializer",
    "ActivityWriteSerializer",
    "AccommodationWriteSerializer",
    "WRITE_SERIALIZERS",
    "CountrySerializer",
    "EmptyQuerySerializer",
    "MarketRefSerializer",
    "MarketSerializer",
    "RegionSerializer",
    "DestinationSerializer",
    "TagSerializer",
    "CancellationPolicySerializer",
    "AttractionSerializer",
    "ActivitySerializer",
    "AccommodationSerializer",
    "MediaSerializer",
    "READ_SERIALIZERS",
    "DestinationQuerySerializer",
    "AttractionQuerySerializer",
    "ActivityQuerySerializer",
    "AccommodationQuerySerializer",
    "SearchQuerySerializer",
    "SearchHitSerializer",
    "NoQuerySerializer",
]


def _degrees(**kwargs: Any) -> serializers.DecimalField:
    """§13.1's exchange format: decimal degrees, at most seven places.

    Seven decimal places is roughly 11 mm — finer than any pickup point needs,
    and the point at which storing more is storing noise. `max_digits` leaves
    room for the three integer digits of 180.
    """
    return serializers.DecimalField(
        max_digits=COORDINATE_PRECISION + 3, decimal_places=COORDINATE_PRECISION, **kwargs
    )


def _money(**kwargs: Any) -> serializers.DecimalField:
    """§7.2's money shape: `NUMERIC(14, 2)`, and never a float.

    `coerce_to_string` is left at DRF's default, so the value leaves as a JSON
    string. A JSON number would be parsed as an IEEE double by every client in
    the stack, which is the exact representation §7.2 forbids.
    """
    return serializers.DecimalField(max_digits=14, decimal_places=2, **kwargs)


# ---------------------------------------------------------------------------
# Write — what an administrator may send
# ---------------------------------------------------------------------------


class _CoordinateWriteSerializer(StrictSerializer):
    """A serializer whose entity carries one point.

    The pair is all-or-nothing on a PATCH. Half a coordinate is a location in
    the sea off West Africa, and accepting one would let a typo move a hotel
    there while reporting success.

    The pair is *not* turned into a geometry here. `latitude` and `longitude`
    leave this layer as the two decimals they arrived as, and
    `services.to_orm_fields` builds the `Point` — the same call the seed
    loader makes, which never sees a serializer at all. Converting in both
    places would be two chances to differ on precision or on axis order, and
    axis order is the one that produces a hotel in the Atlantic.
    """

    latitude = _degrees()
    longitude = _degrees()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        if (attrs.get("latitude") is None) != (attrs.get("longitude") is None):
            raise serializers.ValidationError(
                {"latitude": "Latitude and longitude must be supplied together."}
            )
        return attrs


class CountryWriteSerializer(StrictSerializer):
    """§7.3's `country`.

    `min_latitude`, `min_longitude`, `max_latitude` and `max_longitude` are the
    bounding box of this market. Every coordinate written to a destination,
    attraction, activity or accommodation beneath this country is required to
    fall inside it, which is what catches a latitude and longitude entered the
    wrong way round — both halves of a transposed pair are individually valid.

    Set `min_longitude` greater than `max_longitude` for a country that crosses
    the antimeridian.
    """

    iso_code = serializers.CharField(min_length=2, max_length=2)
    name = serializers.CharField(max_length=80)
    default_currency = serializers.CharField(min_length=3, max_length=3)
    default_timezone = serializers.CharField(max_length=60)
    is_active = serializers.BooleanField(required=False)
    min_latitude = _degrees()
    min_longitude = _degrees()
    max_latitude = _degrees()
    max_longitude = _degrees()


class MarketWriteSerializer(StrictSerializer):
    """ADR 0018.

    `is_active` is optional and the model defaults it to false, for the reason
    `DestinationWriteSerializer` gives one level down: §41.12 has an
    administrator open a market, and "created" and "open" are two decisions.
    `launch_date` is how the second one is scheduled without a deployment.
    """

    country = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=140)
    summary = serializers.CharField(allow_null=True, required=False)
    launch_date = serializers.DateField(allow_null=True, required=False)
    is_active = serializers.BooleanField(required=False)


class RegionWriteSerializer(StrictSerializer):
    #: Both parents, and they must agree. The composite FOREIGN KEY behind
    #: `region(market_id, country_id)` refuses the pair otherwise, so a console
    #: form that mismatched them gets an error rather than a stored
    #: inconsistency.
    country = serializers.UUIDField()
    market = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=140)
    is_active = serializers.BooleanField(required=False)


class DestinationWriteSerializer(_CoordinateWriteSerializer):
    """§7.5.6.

    `is_active` is optional and the model defaults it to false. A market is
    staged, then published — §41.12 asks an administrator to open Arusha, and
    "created" and "open" are two decisions with a review in between.
    """

    region = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    slug = serializers.SlugField(max_length=140)
    summary = serializers.CharField(allow_null=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    timezone = serializers.CharField(max_length=60)
    default_currency = serializers.CharField(min_length=3, max_length=3)
    is_gateway = serializers.BooleanField(required=False)
    gateway_type = serializers.ChoiceField(
        choices=GatewayTypeChoices.choices, allow_null=True, required=False
    )
    gateway_code = serializers.CharField(max_length=10, allow_null=True, required=False)
    launch_date = serializers.DateField(allow_null=True, required=False)
    feature_rank = serializers.IntegerField(min_value=1, required=False)
    is_active = serializers.BooleanField(required=False)


class TagWriteSerializer(StrictSerializer):
    """§24.7's chip vocabulary. A new interest is a row, never a deployment."""

    slug = serializers.SlugField(max_length=64)
    # `label` is also an attribute of DRF's `Field`. `SerializerMetaclass`
    # pops declared fields out of the class body before the class exists, so
    # nothing is shadowed at runtime; the checker cannot see that.
    label = serializers.CharField(max_length=80)  # type: ignore[assignment]
    sort_order = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField(required=False)


class CancellationPolicyTierSerializer(StrictSerializer):
    """One rung of the §14.6 ladder.

    Bounded here as well as by `domain.cancellation.parse_tiers`, because the
    domain rejects rather than repairs and an administrator who typed 150 in a
    percent field deserves to be told which field, not handed a refusal about
    the whole list.
    """

    hours_before = serializers.IntegerField(min_value=0)
    refund_percent = serializers.IntegerField(min_value=0, max_value=100)


class CancellationPolicyWriteSerializer(StrictSerializer):
    """§14.6. Four policies ship as rows; a fifth is a console form.

    `tiers` is ordered most generous first and is validated by
    `domain.cancellation.parse_tiers` at the model tier, which is the one that
    also runs for the seed loader. What this adds is a per-field message.

    BR-106 is why editing this is safe: a booking snapshots the policy in force
    at confirmation, so a change here alters what future bookings are offered
    and never what past ones were sold.
    """

    code = serializers.RegexField(r"^[A-Z0-9_]+$", max_length=32)
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    tiers = CancellationPolicyTierSerializer(many=True)
    is_active = serializers.BooleanField(required=False)


class AttractionWriteSerializer(_CoordinateWriteSerializer):
    """§15.1."""

    destination = serializers.UUIDField()
    name = serializers.CharField(max_length=140)
    slug = serializers.SlugField(max_length=140)
    summary = serializers.CharField(allow_null=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    opening_hours = serializers.JSONField(allow_null=True, required=False)
    entrance_fee = _money(allow_null=True, required=False)
    fee_currency = serializers.CharField(
        min_length=3, max_length=3, allow_null=True, required=False
    )
    visit_minutes = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    tags = serializers.ListField(child=serializers.SlugField(max_length=64), required=False)
    accessibility_notes = serializers.CharField(allow_blank=True, required=False)
    feature_rank = serializers.IntegerField(min_value=1, required=False)
    is_active = serializers.BooleanField(required=False)


class ActivityWriteSerializer(_CoordinateWriteSerializer):
    """§16.1. Administrator-created until the Phase 11 provider portal.

    `price_per_person` and `currency` are both required on create and are
    checked against each other by the model's CHECK constraint. §7.2 does not
    allow an amount without a currency anywhere, and an activity is where that
    would first cost somebody money.
    """

    destination = serializers.UUIDField()
    attraction = serializers.UUIDField(allow_null=True, required=False)
    cancellation_policy = serializers.UUIDField(allow_null=True, required=False)
    name = serializers.CharField(max_length=140)
    slug = serializers.SlugField(max_length=140)
    summary = serializers.CharField(allow_null=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    meeting_point_text = serializers.CharField(max_length=255, allow_blank=True, required=False)
    duration_minutes = serializers.IntegerField(min_value=1)
    price_per_person = _money()
    price_per_group = _money(allow_null=True, required=False)
    currency = serializers.CharField(min_length=3, max_length=3)
    min_pax = serializers.IntegerField(min_value=1, required=False)
    max_pax = serializers.IntegerField(min_value=1)
    min_age = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    requirements = serializers.JSONField(required=False)
    inclusions = serializers.JSONField(required=False)
    exclusions = serializers.JSONField(required=False)
    booking_cutoff_hours = serializers.IntegerField(min_value=0, required=False)
    confirmation_mode = serializers.ChoiceField(choices=ConfirmationMode.choices, required=False)
    tags = serializers.ListField(child=serializers.SlugField(max_length=64), required=False)
    feature_rank = serializers.IntegerField(min_value=1, required=False)
    is_active = serializers.BooleanField(required=False)


class AccommodationWriteSerializer(_CoordinateWriteSerializer):
    """§7.5.7 as amended — ADR 0013.

    There is no rate, no room type, no cancellation policy and no provider to
    send. The field list is the enforcement: a client that posts `base_rate`
    gets a 422 naming it, rather than a 200 that quietly dropped a price.
    """

    destination = serializers.UUIDField()
    name = serializers.CharField(max_length=140)
    slug = serializers.SlugField(max_length=140)
    summary = serializers.CharField(allow_null=True, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    property_type = serializers.ChoiceField(choices=PropertyType.choices)
    address_line = serializers.CharField(max_length=255, allow_blank=True, required=False)
    #: Local wall times in the destination's zone, which is the only zone they
    #: mean anything in. Null where the property has not published them.
    check_in_time = serializers.TimeField(allow_null=True, required=False)
    check_out_time = serializers.TimeField(allow_null=True, required=False)
    feature_rank = serializers.IntegerField(min_value=1, required=False)
    is_active = serializers.BooleanField(required=False)


WRITE_SERIALIZERS: dict[str, type[StrictSerializer]] = {
    "country": CountryWriteSerializer,
    "market": MarketWriteSerializer,
    "region": RegionWriteSerializer,
    "destination": DestinationWriteSerializer,
    "tag": TagWriteSerializer,
    "cancellation_policy": CancellationPolicyWriteSerializer,
    "attraction": AttractionWriteSerializer,
    "activity": ActivityWriteSerializer,
    "accommodation": AccommodationWriteSerializer,
}


# ---------------------------------------------------------------------------
# Read — how a DTO is rendered
# ---------------------------------------------------------------------------
#
# Declared over the DTOs rather than over the models. The DTO is where §7.2's
# "no sequential integers" is already true, so a read serializer cannot leak
# one by naming a field that happens to exist on the row.


class MediaSerializer(serializers.Serializer[Any]):
    """§7.3 `media`, ordered primary-first by `domain.media.order_media`.

    The provenance fields are always present, never conditional. A client
    rendering a credit only when one happens to be in the payload would fail
    open — the licence breach and the correct page look identical from the
    outside — so the shape is fixed and `license_code == ""` is what own work
    looks like.
    """

    file_key = serializers.CharField()
    alt_text = serializers.CharField()
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    is_primary = serializers.BooleanField()
    sort_order = serializers.IntegerField()
    attribution = serializers.CharField(allow_blank=True)
    license_code = serializers.CharField(allow_blank=True)
    license_url = serializers.CharField(allow_blank=True)
    source_url = serializers.CharField(allow_blank=True)


class CountrySerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    iso_code = serializers.CharField()
    name = serializers.CharField()
    default_currency = serializers.CharField()
    default_timezone = serializers.CharField()


class EmptyQuerySerializer(StrictSerializer):
    """Accepts nothing. `StrictSerializer` then makes an unknown parameter a
    422 naming it, rather than a 200 that silently ignored the filter somebody
    thought they were applying."""


class MarketRefSerializer(serializers.Serializer[Any]):
    """A market named. Carries no `is_open` — see `MarketRefDTO` for why."""

    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()


class MarketSerializer(serializers.Serializer[Any]):
    """The destination selector's row. §24.6, ADR 0018."""

    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    summary = serializers.CharField(allow_null=True)
    #: Whether the catalogue beneath this market is browsable *today*. Not the
    #: same question as whether the row came back: the selector lists markets
    #: that are announced and closed, and a client inferring openness from
    #: presence would link a tile at a catalogue that 404s.
    is_open = serializers.BooleanField()
    country = CountrySerializer()


class RegionSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    country = CountrySerializer()
    market = MarketRefSerializer()


class TagSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    slug = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]
    sort_order = serializers.IntegerField()


class CancellationPolicySerializer(serializers.Serializer[Any]):
    """§14.6. `tiers` travels whole: a tourist needs the ladder, not the code."""

    public_id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    tiers = CancellationPolicyTierSerializer(many=True)


class DestinationSerializer(serializers.Serializer[Any]):
    """§7.5.6.

    `timezone` is here because §7.2 renders timestamps in the destination's
    zone and §15.2 evaluates opening hours in it. A client holding a
    destination never has to make a second call to know either.
    """

    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    summary = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    latitude = _degrees(source="centroid.lat")
    longitude = _degrees(source="centroid.lon")
    timezone = serializers.CharField()
    default_currency = serializers.CharField()
    is_gateway = serializers.BooleanField()
    gateway_type = serializers.CharField(allow_null=True)
    gateway_code = serializers.CharField(allow_null=True)
    launch_date = serializers.DateField(allow_null=True)
    feature_rank = serializers.IntegerField()
    region = RegionSerializer()
    media = MediaSerializer(many=True)


class AttractionSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    summary = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    latitude = _degrees(source="coordinates.lat")
    longitude = _degrees(source="coordinates.lon")
    opening_hours = serializers.JSONField(allow_null=True)
    entrance_fee = _money(allow_null=True)
    fee_currency = serializers.CharField(allow_null=True)
    visit_minutes = serializers.IntegerField(allow_null=True)
    tags = serializers.ListField(child=serializers.CharField())
    accessibility_notes = serializers.CharField()
    feature_rank = serializers.IntegerField()
    destination = DestinationSerializer()
    media = MediaSerializer(many=True)


class ActivitySerializer(serializers.Serializer[Any]):
    """§16.1.

    No converted price appears here. §18.4 puts conversion at quote time, and
    a display conversion is an `IndicativeAmount` applied over this — which is
    a different thing with a different label and a different half-life.
    """

    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    summary = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    latitude = _degrees(source="coordinates.lat")
    longitude = _degrees(source="coordinates.lon")
    meeting_point = serializers.CharField()
    duration_minutes = serializers.IntegerField()
    price_per_person = _money()
    price_per_group = _money(allow_null=True)
    currency = serializers.CharField()
    min_pax = serializers.IntegerField()
    max_pax = serializers.IntegerField()
    min_age = serializers.IntegerField(allow_null=True)
    requirements = serializers.JSONField()
    inclusions = serializers.JSONField()
    exclusions = serializers.JSONField()
    booking_cutoff_hours = serializers.IntegerField()
    confirmation_mode = serializers.CharField()
    tags = serializers.ListField(child=serializers.CharField())
    # BR-127: null below `review.min_display_count`, so a client cannot render
    # a mean off one review. `rating_count` still travels, which is what a
    # client needs to render "New" without a second call.
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2, allow_null=True)
    rating_count = serializers.IntegerField()
    feature_rank = serializers.IntegerField()
    destination = DestinationSerializer()
    media = MediaSerializer(many=True)


class AccommodationSerializer(serializers.Serializer[Any]):
    """§7.5.7 and §14 as amended — ADR 0013.

    A location record. There is no rate field to suppress, which is the
    difference between deferring a subsystem and hiding one.
    """

    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    summary = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    property_type = serializers.CharField()
    latitude = _degrees(source="coordinates.lat")
    longitude = _degrees(source="coordinates.lon")
    address_line = serializers.CharField()
    check_in_time = serializers.TimeField(allow_null=True)
    check_out_time = serializers.TimeField(allow_null=True)
    feature_rank = serializers.IntegerField()
    destination = DestinationSerializer()
    media = MediaSerializer(many=True)


# ---------------------------------------------------------------------------
# Query — what a public list endpoint accepts
# ---------------------------------------------------------------------------
#
# Strict, like the write serializers, and for the same reason rather than out of
# symmetry. `?tag=diving` instead of `?tags=diving` is a filter that silently
# does not apply: the caller gets a 200 and a full, unfiltered list, and every
# layer downstream reports success. A 422 naming the parameter is the only
# answer that tells them what happened.
#
# These endpoints are called by our own web tier and by anyone reading the
# published contract, not from a browser address bar carrying campaign
# parameters — so there is no `utm_*` case to be tolerant of.


class _PageQuerySerializer(StrictSerializer):
    """`?limit=&cursor=` — SRS §9.1's pagination parameters.

    `limit` has no bounds here. Its floor and ceiling are `page.default_size`
    and `page.max_size` in `system_setting` (rule 5), applied in the view,
    because a page size an administrator can retune during an incident is the
    whole reason those rows exist.
    """

    limit = serializers.IntegerField(min_value=1, required=False)
    cursor = serializers.CharField(required=False, allow_blank=False)


class NoQuerySerializer(StrictSerializer):
    """An endpoint that takes no parameters at all.

    Declared rather than skipped: without it, `/tags?is_active=false` would be
    a 200 that quietly ignored what was asked for, which is the same failure
    the strict write serializers exist to prevent.
    """


class DestinationQuerySerializer(_PageQuerySerializer):
    region = serializers.SlugField(max_length=140, required=False)
    is_gateway = serializers.BooleanField(required=False)


class _ListingQuerySerializer(_PageQuerySerializer):
    """The filters §24.7 puts on a listing page.

    `tags` is repeatable (`?tags=diving&tags=culture`) rather than
    comma-joined: a comma is a legal character in a slug field elsewhere in
    the API, and two spellings of "a list in a query string" is one more than
    a contract should have.
    """

    destination = serializers.SlugField(max_length=140, required=False)
    tags = serializers.ListField(
        child=serializers.SlugField(max_length=64), required=False, allow_empty=True
    )
    sort = serializers.ChoiceField(choices=[option.value for option in SortOption], required=False)


class AttractionQuerySerializer(_ListingQuerySerializer):
    pass


class ActivityQuerySerializer(_ListingQuerySerializer):
    pass


class SearchQuerySerializer(StrictSerializer):
    """`GET /search?q=` — §24.7.

    Deliberately not a `_PageQuerySerializer`. `/search` is a bounded top-N
    across four tables, not a cursor walk: a keyset over a merged relevance
    ranking would need four positions *and* a rank that stays stable while the
    corpus changes, and `ts_rank` does not. §24.7's box is a jump-to, not a
    browse — the way to see more of one kind is the listing endpoint for it.

    `q` carries no length bounds here. §24.7's two-character minimum is
    `search.min_length` in `system_setting` (rule 5) and is enforced by
    `domain.search.normalise_query`, which is also the seed loader's and the
    console's path. Restating it here would give the rule two homes and one
    would be missed.
    """

    q = serializers.CharField(trim_whitespace=False)
    kind = serializers.ListField(
        child=serializers.ChoiceField(choices=[k.value for k in SearchKind]),
        required=False,
        allow_empty=True,
    )


class SearchHitSerializer(serializers.Serializer[Any]):
    """One result row. Thin on purpose — §24.7 renders a kind, a name and a
    link, and a hit carrying the whole entity would fan out four
    `select_related` trees to draw a line of text."""

    kind = serializers.CharField()
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    destination_slug = serializers.CharField(allow_null=True)
    #: The relevance the row was ordered by. Published because §16.5 commits to
    #: an explainable ordering and a result list is no exception: a provider
    #: asking why they rank where they do can read it.
    rank = serializers.DecimalField(max_digits=12, decimal_places=8)


class AccommodationQuerySerializer(_PageQuerySerializer):
    """§24.11's curated location list.

    No dates, no occupancy, no price sort — ADR 0013. The tourist is naming
    where they are already staying, not shopping, and a parameter that implied
    otherwise would be a promise the Platform does not keep in v1.
    """

    destination = serializers.SlugField(max_length=140, required=False)
    property_type = serializers.ListField(
        child=serializers.ChoiceField(choices=PropertyType.choices),
        required=False,
        allow_empty=True,
    )


READ_SERIALIZERS: dict[str, type[serializers.Serializer[Any]]] = {
    "country": CountrySerializer,
    "market": MarketRefSerializer,
    "region": RegionSerializer,
    "destination": DestinationSerializer,
    "tag": TagSerializer,
    "cancellation_policy": CancellationPolicySerializer,
    "attraction": AttractionSerializer,
    "activity": ActivitySerializer,
    "accommodation": AccommodationSerializer,
}
