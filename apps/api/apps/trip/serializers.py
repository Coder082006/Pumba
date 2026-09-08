"""trip module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). The wire shape, and nothing more.

**Response serializers read DTOs, never models.** The DTO layer already
guarantees no sequential integer reaches a client — §7.2, and
`tests/test_selectors.py` walks the whole graph to prove it. A serializer that
reached past a DTO into an ORM row would be the one way back in, so these take
dataclasses and `ModelSerializer` appears nowhere in this file.

**Request serializers validate shape; the service validates rules.** A
serializer that re-implemented `trip.max_days` would be a second copy of a
threshold that lives in `system_setting` (NFR-M07), and the two would drift the
first time an administrator changed it. So these check types and required
fields, and let `services` refuse a trip that is too long, a destination that
is not visible, or an item type that may not be added by hand.

**`estimate_quality` is always emitted on a transfer.** §12.6 requires the UI
to render an explicit "approximate" label, and the commonest way a label
disappears is a field omitted when it is falsy. It is declared non-optional in
the item serializer and asserted end to end in `test_views.py`, because the
serializer is the last place it can be lost.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.common.serializers import MoneySerializer

__all__ = [
    "ListingRefSerializer",
    "FindingSerializer",
    "TripFlightSerializer",
    "ItineraryItemSerializer",
    "ItinerarySerializer",
    "TripSerializer",
    "TripSummarySerializer",
    "CreateTripSerializer",
    "UpdateTripSerializer",
    "AddItemSerializer",
    "UpdateItemSerializer",
    "FlightInputSerializer",
    "SetFlightsSerializer",
    "TransferEndpointSerializer",
    "TransferLegSerializer",
    "TransferQuoteRequestSerializer",
    "FareBreakdownSerializer",
    "TariffMatchSerializer",
    "FareOptionSerializer",
    "LegQuoteSerializer",
    "CorridorSideSerializer",
    "CorridorSerializer",
]


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class ListingRefSerializer(serializers.Serializer[Any]):
    """`catalogue.dto.ListingRefDTO` — a row named, never numbered."""

    public_id = serializers.UUIDField(read_only=True)
    slug = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)

    #: The IANA zone every instant on this trip is rendered in. Emitted rather
    #: than left to the client to infer: §15.2 evaluates opening hours locally
    #: and §10.4 resolves day boundaries locally, so a browser that formatted
    #: in its own zone would disagree with the plan it was given.
    timezone = serializers.CharField(read_only=True)


class FindingSerializer(serializers.Serializer[Any]):
    """§10.6's `{code, severity, message, item_ids[], suggested_action}`.

    Part of a successful response, not an error channel: §10.2 returns
    findings from a generate that worked, and §24.14 renders them inline
    against the items they name.
    """

    code = serializers.CharField(read_only=True)
    severity = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    item_ids = serializers.ListField(child=serializers.UUIDField(), read_only=True)
    suggested_action = serializers.CharField(read_only=True)

    #: The DTO calls this `context`; the wire calls it `details`.
    #:
    #: `context` is a reserved attribute on a DRF serializer — `self.context`
    #: is how a field reaches the request — so declaring one shadows the
    #: property and breaks the serializer from the inside. mypy caught it here;
    #: at runtime it would have surfaced somewhere unrelated. §10.6 names four
    #: fields and this is not one of them, so the wire name was free to choose.
    details = serializers.DictField(source="context", read_only=True)


class TripFlightSerializer(serializers.Serializer[Any]):
    direction = serializers.CharField(read_only=True)
    flight_number = serializers.CharField(read_only=True)
    airline_iata = serializers.CharField(read_only=True)
    gateway = ListingRefSerializer(read_only=True)
    scheduled_at = serializers.DateTimeField(read_only=True)
    actual_at = serializers.DateTimeField(read_only=True, allow_null=True)
    terminal = serializers.CharField(read_only=True, allow_null=True)
    pax_count = serializers.IntegerField(read_only=True)
    luggage_count = serializers.IntegerField(read_only=True)


class ItineraryItemSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField(read_only=True)
    day_number = serializers.IntegerField(read_only=True)
    sequence_no = serializers.IntegerField(read_only=True)
    item_type = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)

    accommodation = ListingRefSerializer(read_only=True, allow_null=True)
    activity = ListingRefSerializer(read_only=True, allow_null=True)
    attraction = ListingRefSerializer(read_only=True, allow_null=True)
    origin_destination = ListingRefSerializer(read_only=True, allow_null=True)
    target_destination = ListingRefSerializer(read_only=True, allow_null=True)

    distance_m = serializers.IntegerField(read_only=True, allow_null=True)
    travel_seconds = serializers.IntegerField(read_only=True, allow_null=True)

    #: ADR 0019, §12.6. Never omitted — see the module docstring.
    estimate_quality = serializers.CharField(read_only=True, allow_null=True)
    is_approximate = serializers.BooleanField(read_only=True)

    quantity = serializers.IntegerField(read_only=True)
    pax_count = serializers.IntegerField(read_only=True, allow_null=True)
    unit_price = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, allow_null=True
    )
    line_total = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, allow_null=True
    )
    currency = serializers.CharField(read_only=True, allow_null=True)
    is_locked = serializers.BooleanField(read_only=True)


class ItinerarySerializer(serializers.Serializer[Any]):
    version = serializers.IntegerField(read_only=True)
    validation_state = serializers.CharField(read_only=True)
    generated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    total_distance_m = serializers.IntegerField(read_only=True, allow_null=True)
    total_travel_seconds = serializers.IntegerField(read_only=True, allow_null=True)
    items = ItineraryItemSerializer(many=True, read_only=True)
    findings = FindingSerializer(many=True, read_only=True)
    has_errors = serializers.BooleanField(read_only=True)


class TripSummarySerializer(serializers.Serializer[Any]):
    """§24.20's My Trips. Deliberately narrower than `TripSerializer`."""

    public_id = serializers.UUIDField(read_only=True)
    reference = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    destination = ListingRefSerializer(read_only=True)
    start_date = serializers.DateField(read_only=True)
    end_date = serializers.DateField(read_only=True)
    adults = serializers.IntegerField(read_only=True)
    children = serializers.IntegerField(read_only=True)
    infants = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


class TripSerializer(TripSummarySerializer):
    subtotal_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    fee_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    priced_at = serializers.DateTimeField(read_only=True, allow_null=True)
    quote_expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    itinerary = ItinerarySerializer(read_only=True, allow_null=True)
    flights = TripFlightSerializer(many=True, read_only=True)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateTripSerializer(serializers.Serializer[Any]):
    """§10.2's `POST /trips`.

    `destination` is a slug or a UUID and is resolved by
    `catalogue.services.resolve_planning_ref`, which applies visibility — so an
    unopened market's destination is indistinguishable from one that does not
    exist (§30.3). Deciding that here would be a second place to get it wrong.
    """

    destination = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    adults = serializers.IntegerField(min_value=1, default=1)
    children = serializers.IntegerField(min_value=0, default=0)
    infants = serializers.IntegerField(min_value=0, default=0)
    title = serializers.CharField(max_length=140, required=False, allow_null=True)


class UpdateTripSerializer(serializers.Serializer[Any]):
    """`PATCH /trips/{id}`. Every field optional — a PATCH that required them
    all would be a PUT wearing the wrong verb."""

    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    adults = serializers.IntegerField(min_value=1, required=False)
    children = serializers.IntegerField(min_value=0, required=False)
    infants = serializers.IntegerField(min_value=0, required=False)
    title = serializers.CharField(max_length=140, required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("nothing to change")
        return attrs


class AddItemSerializer(serializers.Serializer[Any]):
    """`POST /trips/{id}/items`.

    `item_type` is not constrained to `ADDABLE_ITEM_TYPES` here. The service
    owns that rule — TRANSFER is excluded because §10.4 inserts transfers — and
    duplicating the set would give two places to add a type to.

    **The listing is named, not numbered.** These three fields took the
    catalogue row's integer primary key when the endpoint was first written,
    which made it uncallable: §7.2 forbids returning sequential integers to
    clients, so the catalogue API exposes `public_id` and `slug` and the
    integer a caller would have had to send does not exist outside the
    database. A slug or UUID is what a client actually holds — it is in the
    address bar of the page the tourist is adding from — and it is resolved by
    `catalogue.resolve_listing_ref` exactly as a flight's gateway is.

    **`title` is optional and normally omitted.** The service titles the item
    from the resolved listing's own name, so two tourists adding the same
    activity get the same words. It is accepted for FREE_TIME, which names
    nothing and so has no name to borrow.
    """

    item_type = serializers.CharField()
    day_number = serializers.IntegerField(min_value=1)
    sequence_no = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=160, required=False)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()

    accommodation = serializers.CharField(required=False, allow_null=True)
    activity = serializers.CharField(required=False, allow_null=True)
    attraction = serializers.CharField(required=False, allow_null=True)


class UpdateItemSerializer(serializers.Serializer[Any]):
    """`PATCH /trips/{id}/items/{item_id}` — "modify an unlocked item"."""

    day_number = serializers.IntegerField(min_value=1, required=False)
    sequence_no = serializers.IntegerField(min_value=1, required=False)
    title = serializers.CharField(max_length=160, required=False)
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("nothing to change")
        return attrs


class FlightInputSerializer(serializers.Serializer[Any]):
    """§11.2's `trip_flight`, as a request.

    `gateway` is a slug or UUID, resolved the same way a destination is. §7.5.6's
    `is_gateway` flag is deliberately not required — see `services.set_flights`.
    """

    gateway = serializers.CharField()
    direction = serializers.ChoiceField(choices=["INBOUND", "OUTBOUND"])
    flight_number = serializers.CharField(max_length=10)
    airline_iata = serializers.CharField(max_length=3)
    scheduled_at = serializers.DateTimeField()
    actual_at = serializers.DateTimeField(required=False, allow_null=True)
    terminal = serializers.CharField(max_length=20, required=False, allow_null=True)
    pax_count = serializers.IntegerField(min_value=1)
    luggage_count = serializers.IntegerField(min_value=0, default=0)


class SetFlightsSerializer(serializers.Serializer[Any]):
    """`PUT /trips/{id}/flights` — the whole set.

    An empty list is meaningful and therefore allowed: it is how a tourist
    says they have no flights recorded, and R19's `0..2` includes zero.
    """

    flights = FlightInputSerializer(many=True, allow_empty=True)


# ---------------------------------------------------------------------------
# Transport — §9.3.4's API-04, served here because §12.4 branches on catalogue
# facts and §6.4 forbids `transport -> catalogue` (ADR 0023).
# ---------------------------------------------------------------------------


class TransferEndpointSerializer(serializers.Serializer[Any]):
    """One end of a leg — §12.2's bindings.

    Exactly one of the four references, and an optional precise point *within*
    it. The point is an addition rather than an alternative: §7.5.11 models a
    transfer as `origin_destination_id` **and** `origin_point`, the first
    deciding which tariff applies and the second where the driver stops. A pin
    moved fifty metres must change a metered distance and must never change the
    rung §12.4 answers on.

    Named, not numbered, exactly as `AddItemSerializer` is and for the same
    reason: §7.2 forbids a sequential integer reaching a client, so the integer
    a caller would have had to send does not exist outside the database.
    """

    destination = serializers.CharField(required=False, allow_null=True)
    accommodation = serializers.CharField(required=False, allow_null=True)
    activity = serializers.CharField(required=False, allow_null=True)
    attraction = serializers.CharField(required=False, allow_null=True)

    latitude = serializers.DecimalField(
        max_digits=10, decimal_places=7, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=10, decimal_places=7, required=False, allow_null=True
    )

    #: The four §12.2 bindings that name a row. Declared once so the validator
    #: and the error message cannot disagree about what is accepted.
    NAMED = ("destination", "accommodation", "activity", "attraction")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        named = [key for key in self.NAMED if attrs.get(key)]
        if len(named) != 1:
            raise serializers.ValidationError(
                "A transfer endpoint names exactly one of " + ", ".join(self.NAMED) + "."
            )
        has_lat = attrs.get("latitude") is not None
        has_lng = attrs.get("longitude") is not None
        if has_lat != has_lng:
            raise serializers.ValidationError("A precise point needs both latitude and longitude.")
        return attrs


class TransferLegSerializer(serializers.Serializer[Any]):
    """§9.4.4's `legs[]`.

    `reference` is the client's own handle, echoed back on the answer. §9.4.4
    shows `"leg-1"`; the server never interprets it, which is what lets a
    screen quoting six legs at once match answers to rows without depending on
    ordering.
    """

    reference = serializers.CharField(max_length=64)
    origin = TransferEndpointSerializer()
    target = TransferEndpointSerializer()
    depart_at = serializers.DateTimeField()
    pax = serializers.IntegerField(min_value=1)
    luggage = serializers.IntegerField(min_value=0, default=0)


class TransferQuoteRequestSerializer(serializers.Serializer[Any]):
    """`POST /transport/quotes` — §9.4.4.

    §9.4.4 also shows a top-level `vehicle_class`. It is accepted and echoed as
    a *preference* rather than applied as a filter, because the same section's
    response returns "per-class options" and lists two of them: the field says
    which card to preselect, not which to compute. Filtering on it would make
    §24.16's comparison impossible to render.
    """

    trip_id = serializers.UUIDField()
    legs = TransferLegSerializer(many=True)
    vehicle_class = serializers.CharField(required=False, allow_null=True)

    def validate_legs(self, value: list[Any]) -> list[Any]:
        if not value:
            raise serializers.ValidationError("A quote needs at least one leg.")
        references = [leg["reference"] for leg in value]
        if len(set(references)) != len(references):
            raise serializers.ValidationError("Each leg needs its own reference.")
        return value


class FareBreakdownSerializer(serializers.Serializer[Any]):
    """§9.4.4's `breakdown`. The four parts sum to the price exactly."""

    base = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    distance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    time = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    surcharges = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)


class TariffMatchSerializer(serializers.Serializer[Any]):
    """Which rule priced this option, and on which rung of §12.4's ladder.

    `rule_id` is deliberately absent: it is a sequential integer and §7.2 keeps
    those inside the database. `rule` is its `public_id`, which is what an
    administrator pastes into §27.11's preview tool.
    """

    kind = serializers.CharField(read_only=True)
    rule = serializers.UUIDField(source="rule_public_id", read_only=True)
    step = serializers.IntegerField(read_only=True)


class FareOptionSerializer(serializers.Serializer[Any]):
    """§9.4.4's `options[]` — one vehicle class the party fits, and its fare."""

    vehicle_class = serializers.CharField(source="vehicle_class.code", read_only=True)
    seats = serializers.IntegerField(source="vehicle_class.seats", read_only=True)
    luggage = serializers.IntegerField(source="vehicle_class.luggage_capacity", read_only=True)
    price = MoneySerializer(read_only=True)
    breakdown = FareBreakdownSerializer(read_only=True)
    match = TariffMatchSerializer(read_only=True)


class LegQuoteSerializer(serializers.Serializer[Any]):
    """§9.4.4's response element.

    `estimate_quality` is not in §9.4.4's example and is required by §12.6 and
    ADR 0019: §24.17 must badge a leg whose distance is a haversine estimate,
    and a client cannot badge what it cannot see. It is emitted beside the
    distance rather than instead of it, so the number and the evidence of what
    that number is travel together.

    `polyline` is null until a routing provider is chosen (Appendix D-2). The
    field is published now because §24.17 draws the leg on a map, and a client
    written against a shape that later grows a field has to be rewritten.
    """

    reference = serializers.CharField(source="quote.reference", read_only=True)
    origin = serializers.CharField(source="origin_name", read_only=True)
    target = serializers.CharField(source="target_name", read_only=True)
    distance_m = serializers.IntegerField(read_only=True)
    travel_seconds = serializers.IntegerField(read_only=True)
    estimate_quality = serializers.CharField(read_only=True)
    polyline = serializers.CharField(read_only=True, allow_null=True, default=None)
    options = FareOptionSerializer(many=True, read_only=True, source="quote.options")


class CorridorSideSerializer(serializers.Serializer[Any]):
    """A corridor endpoint, named rather than numbered (§7.2)."""

    slug = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)


class CorridorSerializer(serializers.Serializer[Any]):
    """§9.3.4's `GET /transport/corridors`.

    Where the platform runs transfers, and no fare. A corridor's price depends
    on the class and the date, and this endpoint is public and cacheable; one
    number here would be a price list that goes stale without anything
    happening.
    """

    id = serializers.UUIDField(read_only=True)
    origin = CorridorSideSerializer(read_only=True)
    target = CorridorSideSerializer(read_only=True)
    vehicle_class = serializers.CharField(read_only=True)
    is_bidirectional = serializers.BooleanField(read_only=True)
