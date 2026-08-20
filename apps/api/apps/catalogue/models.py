"""catalogue module — SRS §6.4.

    Owns:       country, region, destination, attraction, activity,
                activity_schedule, accommodation, room_type, media
    Interface:  search_activities(), get_destination(), list_room_types()
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
from django.db import models

from apps.catalogue.domain.hierarchy import GatewayType
from apps.catalogue.validators import (
    validate_cancellation_tiers,
    validate_iana_timezone,
    validate_iso_country_code,
    validate_iso_currency_code,
)
from apps.common.models import SoftDeleteModel

__all__ = [
    "GatewayTypeChoices",
    "Country",
    "Region",
    "Destination",
    "Tag",
    "Attraction",
    "CancellationPolicy",
    "PropertyType",
    "Accommodation",
    "RoomType",
]


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
        ]

    def __str__(self) -> str:
        return self.iso_code


class Region(SoftDeleteModel):
    """§7.3 ERD: country_id, name, is_active. R6 is 1 : 1..* RESTRICT."""

    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="regions")
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
    """SRS §7.5.7 and §14.

    `provider_id` is a plain indexed integer and not a `ForeignKey`: §6.4 gives
    `catalogue` one dependency and it is not `provider`. See ADR 0012 - a
    `ForeignKey` would install a traversable attribute, and the boundary would
    be gone by attribute access rather than by any import anybody wrote.
    """

    #: ADR 0012. Nullable because `provider` arrives around Phase 6, and Phase 3
    #: seeds no accommodation, so nothing carries a dangling reference in the
    #: meantime. The database constraint is added by provider's own migration.
    provider_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)

    destination = models.ForeignKey(
        Destination, on_delete=models.PROTECT, related_name="accommodations"
    )
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=140)
    summary = models.TextField(null=True, blank=True, default=None)
    description = models.TextField(blank=True, default="")

    property_type = models.CharField(max_length=20, choices=PropertyType.choices)
    coordinates = gis_models.PointField(geography=True, srid=4326)
    address_line = models.CharField(max_length=255, blank=True, default="")

    #: §7.5.7 allows null: an unrated guesthouse is not a one-star one, and
    #: §24.12 renders the two differently.
    star_rating = models.SmallIntegerField(null=True, blank=True, default=None)

    #: GIN-indexed, for the §24.11 amenity filter. §14.2 also parks
    #: `pricing_rules` in here for extra-person charges.
    amenities = models.JSONField(default=dict, blank=True)

    #: §14.5: these drive the STAY itinerary item boundaries, and therefore the
    #: timing of the arrival transfer. A local wall time, rendered in
    #: `destination.timezone`, which is the only zone it means anything in.
    check_in_time = models.TimeField(null=True, blank=True, default=None)
    check_out_time = models.TimeField(null=True, blank=True, default=None)

    cancellation_policy = models.ForeignKey(
        CancellationPolicy,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        default=None,
        related_name="accommodations",
    )
    child_policy = models.JSONField(default=dict, blank=True)

    #: BR-103, "default 4". A column rather than a constant, per the standing
    #: rule that thresholds are data.
    booking_cutoff_hours = models.SmallIntegerField(default=4)

    #: §16.5 ranks on rating, and §7.5.3 already denormalises the same pair onto
    #: `provider`. `review` owns the truth and publishes a domain event; this is
    #: a cached projection of it, never written by catalogue from its own
    #: arithmetic.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    rating_count = models.IntegerField(default=0)

    feature_rank = models.SmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

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
                condition=models.Q(star_rating__isnull=True)
                | models.Q(star_rating__gte=1, star_rating__lte=5),
                name="accommodation_star_rating_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(booking_cutoff_hours__gte=0),
                name="accommodation_booking_cutoff_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(rating_avg__gte=0, rating_avg__lte=5),
                name="accommodation_rating_avg_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(rating_count__gte=0),
                name="accommodation_rating_count_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(feature_rank__gte=1),
                name="accommodation_feature_rank_positive",
            ),
        ]
        indexes = [
            GistIndex(fields=["coordinates"], name="accommodation_coordinates_gist"),
            GinIndex(fields=["amenities"], name="accommodation_amenities_gin"),
            models.Index(
                fields=["destination", "is_active", "feature_rank"],
                name="accommodation_dest_active_rank",
            ),
        ]

    def __str__(self) -> str:
        return self.slug


class RoomType(SoftDeleteModel):
    """SRS §7.5.7.

    §14.2 makes rate plans - breakfast-inclusive, non-refundable - separate room
    type rows in V1 rather than a rate-plan dimension, which is why two rows may
    look near-identical and differ only in `base_rate` and policy.
    """

    accommodation = models.ForeignKey(
        Accommodation, on_delete=models.PROTECT, related_name="room_types"
    )
    name = models.CharField(max_length=120)

    #: BR-102: the party must fit, and adults and children count separately.
    #: `max_children = 0` means the room does not accept children at all, which
    #: `domain.occupancy` refuses to price rather than rounding around.
    max_adults = models.SmallIntegerField()
    max_children = models.SmallIntegerField(default=0)

    bed_configuration = models.CharField(max_length=80, blank=True, default="")
    size_sqm = models.SmallIntegerField(null=True, blank=True, default=None)

    #: §14.2: the nightly rate before overrides. §18.5: `Decimal`, never a
    #: float, paired with its currency per §7.2.
    base_rate = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    #: §7.5.7 "Physical inventory ceiling". `room_availability.rooms_open` may
    #: not exceed it; that constraint lives with the availability rows, in
    #: `inventory` (ADR 0011).
    total_rooms = models.SmallIntegerField()

    amenities = models.JSONField(default=dict, blank=True)

    #: The room type's default minimum stay. §14.3 lets a particular night
    #: override it, and that override lives on the availability row.
    min_nights = models.SmallIntegerField(null=True, blank=True, default=None)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "room_type"
        ordering = ["accommodation", "base_rate", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(base_rate__gte=0), name="room_type_base_rate_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(total_rooms__gt=0), name="room_type_total_rooms_positive"
            ),
            # A room that sleeps nobody cannot be sold, and would divide by zero
            # in `domain.occupancy.rooms_required`.
            models.CheckConstraint(
                condition=models.Q(max_adults__gte=1), name="room_type_takes_at_least_one_adult"
            ),
            models.CheckConstraint(
                condition=models.Q(max_children__gte=0), name="room_type_max_children_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(size_sqm__isnull=True) | models.Q(size_sqm__gt=0),
                name="room_type_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(min_nights__isnull=True) | models.Q(min_nights__gt=0),
                name="room_type_min_nights_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.accommodation_id}/{self.name}"
