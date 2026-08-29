"""catalogue module — SRS §6.4.

    Owns:       country, region, destination, attraction, activity,
                activity_schedule, accommodation, cancellation_policy, tag, media
    Interface:  search_activities(), get_destination(), list_accommodation()
    Depends on: location
    Layer:      L1

Data-access layer (SRS §8.2 layer 4).

The geography hierarchy is `country 1..* region 1..* destination`, per the §7.3
ERD and relationship R6 (RESTRICT). `destination` follows §7.5.6 column for
column; `country` and `region` exist only as boxes in the ERD and are designed
here against the §7.2 conventions.

Two things are worth stating outright, because they are what §4.2 asks of this
table set and what §41.12 measures:

**Nothing here knows a destination by name.** Behaviour comes from flags:
`is_gateway`, `timezone`, `default_currency`, `feature_rank`, `is_active`, so
adding Arusha is three console forms and no deployment.

**Coherence is enforced by the database, not by convention.** The gateway
columns are non-null if and only if `is_gateway`; `feature_rank` is positive;
the timezone is a zone the server can actually resolve. Each of those is one
mistyped console field away, and each breaks something far from where it was
typed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.gis.db import models as gis_models
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex, GistIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models

from apps.catalogue.domain.geo import COORDINATE_PRECISION, BoundingBox
from apps.catalogue.domain.hierarchy import GatewayType
from apps.catalogue.domain.requirements import RequirementsError, parse_requirements
from apps.catalogue.validators import (
    validate_activity_requirements,
    validate_cancellation_tiers,
    validate_iana_timezone,
    validate_iso_country_code,
    validate_iso_currency_code,
)
from apps.common.models import SoftDeleteModel, TimestampedModel

__all__ = [
    "GatewayTypeChoices",
    "Country",
    "Market",
    "Region",
    "Destination",
    "Tag",
    "Attraction",
    "CancellationPolicy",
    "PropertyType",
    "Accommodation",
    "ConfirmationMode",
    "Activity",
    "ActivitySchedule",
    "MediaOwnerType",
    "Media",
]


def _degrees() -> models.DecimalField[Decimal, Decimal]:
    """One decimal-degree column, at §13.1's precision.

    Three integer digits so 180 fits, seven fractional so the column cannot
    hold more precision than §13.1 permits to be exchanged — storing more would
    mean a value that survives a round trip through the database but not
    through the API.
    """
    return models.DecimalField(
        max_digits=COORDINATE_PRECISION + 3, decimal_places=COORDINATE_PRECISION
    )


class GatewayTypeChoices(models.TextChoices):
    """§7.5.6: AIRPORT, SEAPORT or LAND_BORDER.

    Built from the domain enum rather than restating it, so the column and
    `hierarchy.validate_gateway` cannot drift apart.
    """

    AIRPORT = GatewayType.AIRPORT.value, "Airport"
    SEAPORT = GatewayType.SEAPORT.value, "Seaport"
    LAND_BORDER = GatewayType.LAND_BORDER.value, "Land border"


class Country(SoftDeleteModel):
    """§7.3 ERD: iso_code (unique), name, currency, timezone, is_active.

    The currency and timezone here are defaults a destination may override.
    §4.2 requires currency to be resolved from `destination.country`, and a
    country large enough to span zones has destinations that disagree with it.
    """

    iso_code = models.CharField(max_length=2, validators=[validate_iso_country_code])
    name = models.CharField(max_length=80)
    default_currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])
    default_timezone = models.CharField(max_length=60, validators=[validate_iana_timezone])
    is_active = models.BooleanField(default=True)

    # The rectangle every coordinate in this market must fall inside. Four
    # columns rather than a PolygonField because this *is* a box — a polygon
    # would invite someone to store a real border, which is a different thing
    # with a different maintenance cost, and `contains` would then reject a
    # legitimate offshore pickup point a few hundred metres out to sea.
    #
    # NOT NULL, deliberately. A nullable bound with a "skip the check if it is
    # absent" rule is an exemption that disables the guard for every row
    # beneath that country, and disables it silently. Opening a market means
    # stating where it is.
    min_latitude = _degrees()
    min_longitude = _degrees()
    max_latitude = _degrees()
    max_longitude = _degrees()

    class Meta:
        db_table = "country"
        ordering = ["name"]
        constraints = [
            # Partial, per §7.7: a soft-deleted country must not hold its ISO
            # code hostage against that market being re-opened later.
            models.UniqueConstraint(
                fields=["iso_code"],
                condition=models.Q(deleted_at__isnull=True),
                name="country_iso_code_unique_alive",
            ),
            # A box with its corners swapped contains nothing, so every write
            # beneath it would fail with a message about the destination rather
            # than about the country that is actually wrong.
            models.CheckConstraint(
                condition=models.Q(min_latitude__lt=models.F("max_latitude")),
                name="country_bounds_latitude_ordered",
            ),
            # Longitude is NOT ordered: min > max is how a box crossing the
            # antimeridian is written (see `geo.BoundingBox`). Only equality is
            # refused, because a zero-width box is a typo in every case.
            models.CheckConstraint(
                condition=~models.Q(min_longitude=models.F("max_longitude")),
                name="country_bounds_longitude_not_degenerate",
            ),
        ]

    def __str__(self) -> str:
        return self.iso_code

    @property
    def bounds(self) -> BoundingBox:
        """The four columns as the domain object that knows what they mean."""
        return BoundingBox(
            min_lat=self.min_latitude,
            min_lon=self.min_longitude,
            max_lat=self.max_latitude,
            max_lon=self.max_longitude,
        )


class Market(SoftDeleteModel):
    """SRS §4.2 as amended to v1.5. ADR 0018.

    The tier a tourist actually chooses between. It exists because the
    five-level model had none: "Zanzibar" is three regions, Pemba is two more,
    Arusha would be one, and no level made the first and the last peers.
    `country` could not serve — both are TZ — and `region` could not, because
    §12.4 step 3 prices the metered transfer fallback per region.

    **Two predicates read this table, and they are deliberately different.**
    `is_listed` (active, not deleted) puts a market in the destination
    selector; `is_open` — `domain.visibility.is_publicly_visible`, with this
    row in the ancestor chain — decides whether its catalogue is browsable.
    A market that is listed and not open is the announced state the landing
    page exists to render, and everything beneath it stays invisible: no
    endpoint, no sitemap entry, 404 on direct URL.

    `launch_date` therefore lives at two levels now, here and on
    `destination`. This is the one §4.1 meant by "scheduled market launch
    without a deployment" — the phrase names this tier, which did not exist
    when it was written.
    """

    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="markets")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)

    #: The line under the tile in the selector, and under the hero on the
    #: market's own page. Nullable like `destination.summary` for the same
    #: reason: a market can be created before anybody has written its copy.
    summary = models.TextField(null=True, blank=True, default=None)

    #: Defaults to `False`, matching `destination`. §41.12 has an administrator
    #: create a market and then open it; a default of `True` would publish it
    #: the moment it was saved, before its regions existed.
    is_active = models.BooleanField(default=False)

    #: §4.1. `None` means "open as soon as `is_active`"; a future date means
    #: listed but not open. The past and today both mean open — a market that
    #: launches on the 12th is open on the 12th.
    launch_date = models.DateField(null=True, blank=True, default=None)

    class Meta:
        db_table = "market"
        ordering = ["country__iso_code", "name"]
        constraints = [
            # Scoped to the country and partial, for the two reasons `region`
            # gives one level down: two countries may each have a coastal
            # market, and §7.7 must not let a withdrawn market hold its slug
            # against being re-opened.
            models.UniqueConstraint(
                fields=["country", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="market_slug_unique_alive_per_country",
            ),
        ]
        indexes = [
            models.Index(fields=["country", "is_active"], name="market_country_active_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class Region(SoftDeleteModel):
    """§7.3 ERD: country_id, name, is_active. R6 is 1 : 1..* RESTRICT.

    `country` is retained alongside `market` rather than reached through it.
    Denormalised on purpose: `country` carries the §13.1 bounding box every
    coordinate beneath it is checked against, and `region.country` is a join
    four `country_path` chains and both `select_related` trees already walk.
    Routing it through `market` would make every bounds check and every
    catalogue read one join deeper.

    A denormalised column that nothing enforces is how two sources of truth
    start, so it is enforced structurally rather than by a service-layer
    check: `market` carries a UNIQUE on `(id, country_id)`, and `region` a
    composite FOREIGN KEY on `(market_id, country_id)` referencing it
    (`0008`, `region_market_shares_country_fk`). PostgreSQL then refuses a
    region whose market belongs to a different country. Django models neither
    constraint, so both are raw SQL — the same treatment ADR 0012's
    cross-module keys get in `inventory/0001`.
    """

    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="regions")

    #: ADR 0018. NOT NULL: a region with no market is a region no selector can
    #: reach and no visibility chain can hide, which is the failure the whole
    #: tier was added to prevent.
    market = models.ForeignKey(Market, on_delete=models.PROTECT, related_name="regions")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "region"
        ordering = ["country__iso_code", "name"]
        constraints = [
            # Scoped to the country rather than global: two countries may each
            # have a northern region, and neither should block the other.
            models.UniqueConstraint(
                fields=["country", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="region_slug_unique_alive_per_country",
            ),
        ]
        indexes = [models.Index(fields=["country", "is_active"], name="region_country_active_idx")]

    def __str__(self) -> str:
        return self.name


class Destination(SoftDeleteModel):
    """SRS §7.5.6, in full."""

    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name="destinations")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)

    #: §7.5.6 "Catalogue copy": the one-line blurb on a card, and the source of
    #: the `generateMetadata` description.
    summary = models.TextField(null=True, blank=True, default=None)
    #: §24.8 puts a description on the page beneath the hero. A card blurb and
    #: a page body are different lengths for different places, and collapsing
    #: them into one column means one of the two always reads badly.
    description = models.TextField(blank=True, default="")

    #: §13.1 forbids planar approximations, so this is `geography`, not
    #: `geometry`: `ST_Distance` then returns geodesic metres.
    centroid = gis_models.PointField(geography=True, srid=4326)

    is_gateway = models.BooleanField(default=False)
    gateway_type = models.CharField(
        max_length=20, choices=GatewayTypeChoices.choices, null=True, blank=True, default=None
    )
    gateway_code = models.CharField(max_length=10, null=True, blank=True, default=None)

    #: An IANA name, never a UTC offset: an offset loses the DST rules, and
    #: §15.2 evaluates opening hours in this zone.
    timezone = models.CharField(max_length=60, validators=[validate_iana_timezone])
    default_currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    #: §7.5.6 "Visibility gate". Null means no gate; `domain.visibility` treats
    #: the launch day itself as visible.
    launch_date = models.DateField(null=True, blank=True, default=None)

    #: §16.5 sorts this ASCENDING, so 1 is the most featured. Constrained
    #: positive because 0 would outrank every curated destination, silently.
    feature_rank = models.SmallIntegerField(default=100)

    #: §7.5.6 defaults this to false. A destination stays invisible until
    #: somebody decides it is ready, which is the safer direction for the
    #: mistake to fall in.
    is_active = models.BooleanField(default=False)

    #: §7.6: `GIN(to_tsvector(name || description))`.
    #:
    #: A **generated stored column**, not a trigger and not application code.
    #: `to_tsvector(regconfig, text)` is immutable, so PostgreSQL maintains it
    #: on every write; there is no code path that can forget to update it and
    #: no window in which a row and its index disagree.
    #:
    #: The dictionary is fixed at `english`, which is a real limitation rather
    #: than a §4.2 violation: it is a stemming rule, not destination logic, and
    #: it is what makes "beaches" find "beach". A market whose catalogue copy is
    #: not in English wants a per-row `regconfig`, which a generated column can
    #: take as long as the config is a column of that type. That is a schema
    #: change and a re-index, and it belongs to the phase that opens such a
    #: market rather than to a guess made now.
    search_vector = models.GeneratedField(
        expression=SearchVector("name", "summary", "description", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "destination"
        ordering = ["feature_rank", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="destination_slug_unique_alive",
            ),
            # §7.5.6: "partial UNIQUE(gateway_code) WHERE is_gateway".
            models.UniqueConstraint(
                fields=["gateway_code"],
                condition=models.Q(is_gateway=True, deleted_at__isnull=True),
                name="destination_gateway_code_unique_alive",
            ),
            # If and only if, in both directions. A `gateway_code` on a row
            # with `is_gateway = false` sits outside the partial index above,
            # so a later gateway can claim the same code.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_gateway=True, gateway_type__isnull=False, gateway_code__isnull=False
                    )
                    | models.Q(
                        is_gateway=False, gateway_type__isnull=True, gateway_code__isnull=True
                    )
                ),
                name="destination_gateway_columns_coherent",
            ),
            models.CheckConstraint(
                condition=models.Q(feature_rank__gte=1),
                name="destination_feature_rank_positive",
            ),
        ]
        indexes = [
            # §7.5.6: "GIST(centroid); INDEX(region_id, is_active)".
            GistIndex(fields=["centroid"], name="destination_centroid_gist"),
            models.Index(fields=["region", "is_active"], name="destination_region_active_idx"),
            GinIndex(fields=["search_vector"], name="destination_search_gin"),
        ]

    def __str__(self) -> str:
        return self.slug

    @property
    def today_local(self) -> date:
        """The current date where the destination is, not where the server is.

        A destination launching on 1 September launches at midnight local. A
        server reasoning in UTC opens it three hours early in East Africa and
        most of a day late in Auckland.
        """
        return datetime.now(tz=ZoneInfo(self.timezone)).date()


class Tag(SoftDeleteModel):
    """The §24.7 category chips, as rows.

    §24.7 names them: beaches, heritage, water sports, nature, culture. Those
    are Zanzibar-shaped words, and §4.2 prohibits them appearing in application
    code, so the vocabulary is administrator-managed data. Adding "diving" is a
    row, and it reaches the chip strip with no deployment.

    `attraction.tags` and `activity.tags` reference this by slug in a `text[]`
    rather than through a join table, because §16.5 filters with the `&&`
    overlap operator. The array is not a foreign key, so a trigger keeps the
    vocabulary closed - see `apps.catalogue.db`.
    """

    slug = models.SlugField(max_length=64)
    label = models.CharField(max_length=80)
    #: The chip strip is ordered by a curator, not alphabetically: the order
    #: is editorial, and §16.5's determinism applies to it as much as to
    #: results. `id` breaks ties.
    sort_order = models.SmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "tag"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tag_slug_unique_alive",
            ),
        ]

    def __str__(self) -> str:
        return self.slug


class Attraction(SoftDeleteModel):
    """SRS §15.1.

    A place of interest, not a bookable product: it exists to help a tourist
    decide where to go, and to give the itinerary a geographic anchor. Where an
    attraction can be visited commercially, a provider publishes an `activity`
    against it (§15.4) - which is why there is no price, no capacity and no
    provider here.
    """

    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="attractions"
    )
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=140)
    summary = models.TextField(null=True, blank=True, default=None)
    description = models.TextField(blank=True, default="")

    #: §13.1: geography, so `ST_Distance` returns geodesic metres.
    coordinates = gis_models.PointField(geography=True, srid=4326)

    #: §15.2's fixed JSONB schema, parsed by `domain.opening_hours` and
    #: **evaluated in `destination.timezone`**, never the server's. Null means
    #: "hours not published", which is not the same as closed and is rendered
    #: differently.
    opening_hours = models.JSONField(null=True, blank=True, default=None)

    #: §15.3: informational in V1. The tourist pays this on site, and it is
    #: excluded from the trip total with that exclusion stated explicitly on
    #: the cost breakdown. Never an input to any subtotal.
    entrance_fee = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    #: §7.2: "Never store money without its currency." The CHECK below makes
    #: the pair all-or-nothing rather than trusting the writer.
    fee_currency = models.CharField(
        max_length=3, null=True, blank=True, default=None, validators=[validate_iso_currency_code]
    )

    #: §15.1 "recommended visit duration"; §15.5 spends it in the itinerary.
    visit_minutes = models.SmallIntegerField(null=True, blank=True, default=None)

    #: Slugs from `tag`. `text[]` rather than a join table because §16.5
    #: filters with `tags && :interest_tags`; a trigger keeps them known.
    tags = ArrayField(models.SlugField(max_length=64), default=list, blank=True)

    accessibility_notes = models.TextField(blank=True, default="")
    feature_rank = models.SmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    search_vector = models.GeneratedField(
        expression=SearchVector("name", "summary", "description", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "attraction"
        ordering = ["feature_rank", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="attraction_slug_unique_alive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(entrance_fee__isnull=True, fee_currency__isnull=True)
                    | models.Q(entrance_fee__isnull=False, fee_currency__isnull=False)
                ),
                name="attraction_fee_has_a_currency",
            ),
            # A free attraction is 0.00, not -1. §15.3 shows this figure to a
            # tourist as an on-site cost.
            models.CheckConstraint(
                condition=models.Q(entrance_fee__isnull=True) | models.Q(entrance_fee__gte=0),
                name="attraction_fee_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(visit_minutes__isnull=True) | models.Q(visit_minutes__gt=0),
                name="attraction_visit_minutes_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(feature_rank__gte=1),
                name="attraction_feature_rank_positive",
            ),
        ]
        indexes = [
            GistIndex(fields=["coordinates"], name="attraction_coordinates_gist"),
            GinIndex(fields=["tags"], name="attraction_tags_gin"),
            models.Index(
                fields=["destination", "is_active", "feature_rank"],
                name="attraction_dest_active_rank",
            ),
            GinIndex(fields=["search_vector"], name="attraction_search_gin"),
        ]

    def __str__(self) -> str:
        return self.slug

    @property
    def timezone(self) -> str:
        """The zone §15.2 says opening hours are evaluated in.

        Read from the destination rather than stored here, so a destination
        that corrects its zone corrects every attraction in it at once.
        """
        return self.destination.timezone


class CancellationPolicy(SoftDeleteModel):
    """SRS §14.6, referenced by properties and activities.

    §14.6 names four policies and then says the thing that matters: a policy is
    an ordered list of `{hours_before, refund_percent}` tiers, so that
    administrators can create new ones without code changes. The four codes are
    therefore rows here and appear nowhere in executable code, and a market with
    different consumer law gets new rows rather than a release.

    BR-106: the policy in force on a booking is the one snapshotted at
    confirmation, not this row as it stands later. Editing a policy changes what
    future bookings are offered, never what past ones were sold.
    """

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    #: An ordered list, most generous first. `domain.cancellation.parse_tiers`
    #: reads it and rejects rather than repairs; a CHECK could only reach as far
    #: as "is an array", which catches the gross shape error and nothing else.
    tiers = models.JSONField(default=list, blank=True, validators=[validate_cancellation_tiers])

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "cancellation_policy"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(deleted_at__isnull=True),
                name="cancellation_policy_code_unique_alive",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class PropertyType(models.TextChoices):
    """§7.5.7: HOTEL, RESORT, LODGE, GUESTHOUSE or APARTMENT.

    Structural categories from the specification rather than destination
    vocabulary, so unlike the §24.7 chips these are choices and not rows.
    """

    HOTEL = "HOTEL", "Hotel"
    RESORT = "RESORT", "Resort"
    LODGE = "LODGE", "Lodge"
    GUESTHOUSE = "GUESTHOUSE", "Guesthouse"
    APARTMENT = "APARTMENT", "Apartment"


class Accommodation(SoftDeleteModel):
    """SRS §7.5.7 and §14, as amended in v1.2 — ADR 0013.

    **This is a location record, not a product.** It holds where a property is
    and when its day starts and ends, and nothing about price, capacity or
    availability. A STAY itinerary item anchors to one of these (or to a
    free-entry address with a confirmed geocode, §13.2) and carries no
    provider, no price, no booking and no inventory.

    That is why it is administrator-curated in the same way `attraction` is,
    and why Appendix C can seed roughly forty Zanzibar properties: seeding a
    location asserts nothing that only its owner could assert. The columns that
    did make such assertions — `provider_id`, `star_rating`, `amenities`,
    `child_policy`, `cancellation_policy_id`, `booking_cutoff_hours` and the
    denormalised rating pair — left with the subsystem and return with it in
    v2, along with `room_type`.
    """

    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="accommodations"
    )
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=140)
    summary = models.TextField(null=True, blank=True, default=None)
    description = models.TextField(blank=True, default="")

    property_type = models.CharField(max_length=20, choices=PropertyType.choices)

    #: The whole point of the record. §12.4 prices the transfer from here, and
    #: a curated property quotes to the metre where a geocoded address does not.
    coordinates = gis_models.PointField(geography=True, srid=4326)
    address_line = models.CharField(max_length=255, blank=True, default="")

    #: §14.5: these bound the STAY item, and therefore the timing of the
    #: arrival transfer and any first-day activity. A local wall time, rendered
    #: in `destination.timezone`, which is the only zone it means anything in.
    #: Null where the property has not published one; §24.11 falls back to the
    #: destination defaults in Appendix B.
    check_in_time = models.TimeField(null=True, blank=True, default=None)
    check_out_time = models.TimeField(null=True, blank=True, default=None)

    feature_rank = models.SmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    search_vector = models.GeneratedField(
        expression=SearchVector("name", "summary", "description", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "accommodation"
        ordering = ["feature_rank", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="accommodation_slug_unique_alive",
            ),
            models.CheckConstraint(
                condition=models.Q(feature_rank__gte=1),
                name="accommodation_feature_rank_positive",
            ),
        ]
        indexes = [
            GistIndex(fields=["coordinates"], name="accommodation_coordinates_gist"),
            models.Index(
                fields=["destination", "is_active", "feature_rank"],
                name="accommodation_dest_active_rank",
            ),
            GinIndex(fields=["search_vector"], name="accommodation_search_gin"),
        ]

    def __str__(self) -> str:
        return self.slug


class ConfirmationMode(models.TextChoices):
    """§16.6: INSTANT or ON_REQUEST.

    INSTANT means capacity is authoritative and the booking confirms on
    payment. ON_REQUEST means the booking enters AWAITING_PROVIDER and the
    provider must accept within `provider_response_hours` or it auto-cancels
    with a full refund. §16.6 requires the catalogue to display the mode
    clearly, which is why it is a catalogue column and not a booking one.
    """

    INSTANT = "INSTANT", "Confirms immediately"
    ON_REQUEST = "ON_REQUEST", "Confirmed by the provider"


class Activity(SoftDeleteModel):
    """SRS §16.1 and §7.5.9.

    §16.1 is unambiguous: activities are entirely database-driven, and no
    activity type, category or name appears in application code. Snorkelling,
    spice farms and dhow cruises are rows.

    §15.4 makes `attraction` optional. An open-water excursion anchors on its
    own coordinates; a heritage walk anchors on the attraction it visits.
    """

    #: ADR 0012, as on `accommodation`.
    provider_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)

    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="activities"
    )
    #: §15.4: null for an activity with no fixed site.
    attraction = models.ForeignKey(
        Attraction,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        default=None,
        related_name="activities",
    )

    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=140)
    summary = models.TextField(null=True, blank=True, default=None)
    description = models.TextField(blank=True, default="")

    coordinates = gis_models.PointField(geography=True, srid=4326)
    meeting_point_text = models.CharField(max_length=255, blank=True, default="")

    duration_minutes = models.SmallIntegerField()

    #: §18.5: Decimal, never a float, paired with a currency per §7.2. The
    #: group price wins where both exist - see `domain.pricing`.
    price_per_person = models.DecimalField(max_digits=14, decimal_places=2)
    price_per_group = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    min_pax = models.SmallIntegerField(default=1)
    max_pax = models.SmallIntegerField()

    #: §7.5.9 keeps this as a column and §16.4 also carries `min_age` inside
    #: `requirements`. The column is what the booking guard reads; the JSONB is
    #: what VR-15 explains to the tourist. A validator keeps them agreeing.
    min_age = models.SmallIntegerField(null=True, blank=True, default=None)

    #: §16.4: structured, machine-checkable restrictions feeding VR-15.
    requirements = models.JSONField(
        default=dict, blank=True, validators=[validate_activity_requirements]
    )
    inclusions = models.JSONField(default=list, blank=True)
    exclusions = models.JSONField(default=list, blank=True)

    #: Slugs from `tag`, kept honest by the same trigger as `attraction.tags`.
    tags = ArrayField(models.SlugField(max_length=64), default=list, blank=True)

    cancellation_policy = models.ForeignKey(
        CancellationPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        default=None,
        related_name="activities",
    )

    #: §16.6.
    booking_cutoff_hours = models.SmallIntegerField(default=24)
    confirmation_mode = models.CharField(
        max_length=20, choices=ConfirmationMode.choices, default=ConfirmationMode.INSTANT
    )

    #: §16.5 ranks on these. `review` owns the truth; this is its projection.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    rating_count = models.IntegerField(default=0)

    feature_rank = models.SmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    search_vector = models.GeneratedField(
        expression=SearchVector("name", "summary", "description", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "activity"
        ordering = ["feature_rank", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="activity_slug_unique_alive",
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_person__gte=0), name="activity_price_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(price_per_group__isnull=True) | models.Q(price_per_group__gte=0),
                name="activity_group_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0), name="activity_duration_positive"
            ),
            # §16.3's bookable predicate is `min_pax <= pax <= max_pax`. An
            # inverted pair makes it unsatisfiable, so the activity is listed
            # and can never be booked - visible and inert, which is worse than
            # absent.
            models.CheckConstraint(
                condition=models.Q(min_pax__gte=1, max_pax__gte=1)
                & models.Q(min_pax__lte=models.F("max_pax")),
                name="activity_pax_range_is_satisfiable",
            ),
            models.CheckConstraint(
                condition=models.Q(min_age__isnull=True) | models.Q(min_age__gte=0),
                name="activity_min_age_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(booking_cutoff_hours__gte=0),
                name="activity_booking_cutoff_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(rating_avg__gte=0, rating_avg__lte=5),
                name="activity_rating_avg_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(rating_count__gte=0), name="activity_rating_count_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(feature_rank__gte=1), name="activity_feature_rank_positive"
            ),
        ]
        indexes = [
            GistIndex(fields=["coordinates"], name="activity_coordinates_gist"),
            GinIndex(fields=["tags"], name="activity_tags_gin"),
            # The leading edge of the §16.5 ordering: destination match, then
            # curation. `price_per_person` trails so the index also serves the
            # price_asc sort without a second scan.
            models.Index(
                fields=["destination", "is_active", "feature_rank", "price_per_person"],
                name="activity_dest_active_rank",
            ),
            models.Index(fields=["attraction"], name="activity_attraction_idx"),
            GinIndex(fields=["search_vector"], name="activity_search_gin"),
        ]

    def __str__(self) -> str:
        return self.slug

    def clean(self) -> None:
        """Keep the `min_age` column and the §16.4 JSONB from disagreeing.

        Both exist because §7.5.9 lists the column and §16.4 lists the key. The
        column is what a booking guard reads; the JSONB is what VR-15 explains
        to the tourist. Two sources for one safety control is one too many, so
        the console refuses to save them saying different things rather than
        letting a booking be admitted that the listing says it forbids.
        """
        super().clean()
        try:
            parsed = parse_requirements(self.requirements or {})
        except RequirementsError:
            # `full_clean` runs `clean()` even when `clean_fields()` has
            # already rejected something, accumulating errors rather than
            # stopping. The field validator has reported this one; raising it
            # again here would replace a field-scoped message with an
            # unhandled exception.
            return
        disagree = (
            parsed.min_age is not None
            and self.min_age is not None
            and parsed.min_age != self.min_age
        )
        if disagree:
            raise ValidationError(
                {
                    "min_age": (
                        f"min_age is {self.min_age} but requirements say "
                        f"{parsed.min_age}; they must agree"
                    )
                }
            )


class ActivitySchedule(SoftDeleteModel):
    """SRS §16.2: a recurring rule, not a sellable thing.

    §16.2 separates the two deliberately. This row says "Monday to Saturday at
    08:30, capacity 12, during 2027"; `inventory.activity_departure` says
    "08:30 on 12 August, six sold". The nightly materialisation that turns one
    into the other is Phase 5 and lives in `inventory` (ADR 0011).

    `weekday_mask` has bit 0 as Monday, matching `date.weekday()` - see
    `domain.schedules`, which owns the arithmetic.
    """

    activity = models.ForeignKey(Activity, on_delete=models.PROTECT, related_name="schedules")

    weekday_mask = models.SmallIntegerField()
    start_time = models.TimeField()

    #: Capacity for departures generated from this rule. The generated
    #: departure carries its own counters; changing this changes what future
    #: materialisation produces, never what a sold departure holds.
    capacity = models.SmallIntegerField()

    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True, default=None)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "activity_schedule"
        ordering = ["activity", "start_time", "id"]
        constraints = [
            # A mask of zero recurs on no day at all: a rule that looks like a
            # schedule and generates nothing, forever.
            models.CheckConstraint(
                condition=models.Q(weekday_mask__gte=1, weekday_mask__lte=0b1111111),
                name="activity_schedule_weekday_mask_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0), name="activity_schedule_capacity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gte=models.F("valid_from")),
                name="activity_schedule_window_is_ordered",
            ),
        ]
        indexes = [
            models.Index(fields=["activity", "is_active"], name="activity_schedule_active_idx")
        ]

    def __str__(self) -> str:
        return f"{self.activity_id}@{self.start_time}"


class MediaOwnerType(models.TextChoices):
    """What a `media` row hangs off.

    Stored as the owning table's name rather than a Django content type: §7.3
    models `media` as a plain polymorphic pair, and `django.contrib.contenttypes`
    would put a second registry of models into the schema and a join into every
    gallery query.
    """

    #: ADR 0018. A market owns the photography its landing page leads with,
    #: which is what makes "the hero shows the place you chose" data rather
    #: than a mapping in code (§4.2). Opening a market is a row and its
    #: pictures.
    MARKET = "market", "Market"
    DESTINATION = "destination", "Destination"
    ATTRACTION = "attraction", "Attraction"
    ACTIVITY = "activity", "Activity"
    ACCOMMODATION = "accommodation", "Accommodation"

    #: Reserved, not withdrawn. `room_type` is v2 (ADR 0013), but this value is
    #: a persisted string in a column, and the rule for those is the same one
    #: §20.2 applies to `booking_type = ACCOMMODATION`: leave the value in
    #: place so reviving the subsystem renumbers nothing.
    ROOM_TYPE = "room_type", "Room type"


class Media(TimestampedModel):
    """SRS §7.3 and §35.7.

    Polymorphic by `(owner_type, owner_id)`. Ordering is primary first, then
    `sort_order`, then `id` - `domain.media` owns that and the reason: a gallery
    whose second and third images swap between page loads shifts under the
    reader, which is a Lighthouse CLS failure as well as an irritation.

    `width` and `height` are stored because `next/image` needs explicit
    dimensions to reserve space before the image loads. A row without them is a
    row the console should not have accepted, and the §24 CLS budget is what
    pays for it.

    Not soft-deleted: a removed image is removed. There is no re-registration
    case, and §7.7's uniqueness argument does not apply.
    """

    owner_type = models.CharField(max_length=20, choices=MediaOwnerType.choices)
    owner_id = models.BigIntegerField()

    #: §35.7: content-hashed, so a variant URL is a pure function of
    #: (key, width, format) and the CDN can cache it for a year.
    file_key = models.CharField(max_length=255)
    alt_text = models.CharField(max_length=255, blank=True, default="")
    width = models.IntegerField(null=True, blank=True, default=None)
    height = models.IntegerField(null=True, blank=True, default=None)

    sort_order = models.SmallIntegerField(default=0)
    is_primary = models.BooleanField(default=False)

    # --- Provenance ------------------------------------------------------
    #
    # This table stored a file key, a caption and two dimensions and nothing
    # about where the picture came from. That is fine while every photograph
    # is your own and untenable the moment one is not: attribution is a
    # *condition* of CC BY, so an image licensed that way could not lawfully
    # be displayed by a schema with nowhere to put the credit. The constraint
    # below is what makes that structural rather than a habit.
    #
    # `license_code` empty means own work — the only exemption, and an honest
    # one. Anything else must say who made it and under what.

    #: The credit line as the source published it, not as somebody retyped it.
    #: For Commons this is `extmetadata.Artist`, captured at fetch time.
    attribution = models.CharField(max_length=255, blank=True, default="")

    #: Short licence identifier: "CC BY 4.0", "CC0", "PD". Free text rather
    #: than a choice list, because the set of licences a photograph can arrive
    #: under is not something this application gets to enumerate, and a new one
    #: must not require a migration (§4.2's reasoning, applied to metadata).
    license_code = models.CharField(max_length=40, blank=True, default="")

    #: Where the licence text lives. CC BY requires the licence to be
    #: identifiable, not merely named.
    license_url = models.URLField(max_length=500, blank=True, default="")

    #: The file's page at its source, so a claim can be checked rather than
    #: trusted. Optional: own work has no source page.
    source_url = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "media"
        ordering = ["owner_type", "owner_id", "-is_primary", "sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(width__isnull=True) | models.Q(width__gt=0),
                name="media_width_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(height__isnull=True) | models.Q(height__gt=0),
                name="media_height_positive",
            ),
            # One hero per owner. `domain.media.order_media` survives two
            # primaries deterministically rather than raising, because a
            # gallery that refuses to render is worse than one that picks the
            # lower id - but the schema should not let it happen in the first
            # place.
            models.UniqueConstraint(
                fields=["owner_type", "owner_id"],
                condition=models.Q(is_primary=True),
                name="media_one_primary_per_owner",
            ),
            # A row that names a licence must carry the credit and the licence
            # URL that licence requires. Own work — `license_code = ''` — is
            # exempt, and is the only exemption.
            #
            # In the database rather than in a serializer for the same reason
            # `media_one_primary_per_owner` is: the console should never
            # accept such a row, and "should never" is not a guarantee. The
            # difference here is that the failure is not a cosmetic one. An
            # uncredited CC BY photograph on a commercial page is a licence
            # breach, and it looks exactly like a correctly credited one to
            # everything except a lawyer.
            models.CheckConstraint(
                condition=models.Q(license_code="")
                | (~models.Q(attribution="") & ~models.Q(license_url="")),
                name="media_licensed_rows_carry_attribution",
            ),
        ]
        indexes = [
            models.Index(fields=["owner_type", "owner_id"], name="media_owner_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.owner_type}:{self.owner_id}/{self.file_key}"
