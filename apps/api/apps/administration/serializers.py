"""Interface layer (SRS §8.2 layer 1).

§27.11's tariff console: "CRUD over transfer corridors and tariffs, with a
quote-preview tool for any origin-destination-class combination".

**Why these shapes live in `administration` and not in `transport`.** A corridor
names two destinations and a tariff names a region or a country, all of them
`catalogue` rows; §6.4 gives `transport -> location, provider`, so the module
that owns the tables cannot resolve the names an administrator types. ADR 0023
puts the resolving here, in the module §6.4 gives "all (read via interfaces)".

**Named, never numbered.** An administrator writes `"nungwi"` and `"TZ"`,
because those are what the console shows and what a person can check. §7.2 keeps
the integers inside the database, and the service turns one into the other.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.common.serializers import StrictSerializer

__all__ = [
    "CorridorWriteSerializer",
    "TariffWriteSerializer",
    "QuotePreviewSerializer",
    "CorridorReadSerializer",
    "TariffReadSerializer",
    "PreviewOptionSerializer",
    "QuotePreviewResultSerializer",
    "ProviderWriteSerializer",
    "ProviderReadSerializer",
    "ProviderStatusSerializer",
    "ProviderStatusResultSerializer",
    "ActivityProviderSerializer",
]


class CorridorWriteSerializer(StrictSerializer):
    """§12.4's fixed-price route, as §27.11's form submits it."""

    origin_destination = serializers.CharField(required=False)
    target_destination = serializers.CharField(required=False)
    vehicle_class = serializers.CharField(required=False)
    fixed_price = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    currency = serializers.CharField(max_length=3, required=False)
    is_bidirectional = serializers.BooleanField(required=False)
    valid_from = serializers.DateField(required=False)
    valid_to = serializers.DateField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class TariffWriteSerializer(StrictSerializer):
    """§12.4's metered fallback.

    `region` and `country` are both optional and exactly one is required, which
    the service enforces rather than this: the rule is "a tariff answers on one
    rung", the database says so with a CHECK, and a third statement of it here
    would be a third place to disagree.

    `waiting_rate_per_minute` is accepted and stored and read by nothing —
    §12.4 applies it "after free waiting allowance" and defines no such
    allowance. `apps/transport/tests/test_tariff_model.py` asserts the silence.
    """

    scope = serializers.ChoiceField(choices=["REGION", "COUNTRY"], required=False)
    region = serializers.CharField(required=False, allow_null=True)
    country = serializers.CharField(required=False, allow_null=True)
    vehicle_class = serializers.CharField(required=False)

    base_fare = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    per_km_rate = serializers.DecimalField(max_digits=14, decimal_places=4, required=False)
    per_minute_rate = serializers.DecimalField(max_digits=14, decimal_places=4, required=False)
    minimum_fare = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)

    night_surcharge_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    night_from = serializers.TimeField(required=False, allow_null=True)
    night_to = serializers.TimeField(required=False, allow_null=True)

    airport_surcharge = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    waiting_rate_per_minute = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False
    )

    currency = serializers.CharField(max_length=3, required=False)
    valid_from = serializers.DateField(required=False)
    valid_to = serializers.DateField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class QuotePreviewSerializer(StrictSerializer):
    """§27.11's "quote-preview tool for any origin-destination-class
    combination".

    `pickup_at` is not in §27.11's one-line description and is accepted anyway.
    §12.4's night surcharge is evaluated against "pickup_at local time", so a
    preview with no instant could not show an administrator the surcharged
    price at all — which is the price most worth previewing, because it is the
    one a tourist queries.
    """

    origin_destination = serializers.CharField()
    target_destination = serializers.CharField()
    pax = serializers.IntegerField(min_value=1, default=2)
    luggage = serializers.IntegerField(min_value=0, default=2)
    pickup_at = serializers.DateTimeField(required=False)
    distance_m = serializers.IntegerField(required=False, min_value=0)
    travel_seconds = serializers.IntegerField(required=False, min_value=0)


class CorridorReadSerializer(serializers.Serializer[Any]):
    """What the console gets back. Ids are `public_id`s (§7.2)."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    origin_destination = serializers.CharField(read_only=True)
    target_destination = serializers.CharField(read_only=True)
    vehicle_class = serializers.CharField(read_only=True)
    fixed_price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    is_bidirectional = serializers.BooleanField(read_only=True)
    valid_from = serializers.DateField(read_only=True)
    valid_to = serializers.DateField(read_only=True, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)


class TariffReadSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="public_id", read_only=True)
    scope = serializers.CharField(read_only=True)
    region = serializers.CharField(read_only=True, allow_null=True)
    country = serializers.CharField(read_only=True, allow_null=True)
    vehicle_class = serializers.CharField(read_only=True)
    base_fare = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    per_km_rate = serializers.DecimalField(max_digits=14, decimal_places=4, read_only=True)
    per_minute_rate = serializers.DecimalField(max_digits=14, decimal_places=4, read_only=True)
    minimum_fare = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    night_surcharge_pct = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    night_from = serializers.TimeField(read_only=True, allow_null=True)
    night_to = serializers.TimeField(read_only=True, allow_null=True)
    airport_surcharge = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    waiting_rate_per_minute = serializers.DecimalField(
        max_digits=14, decimal_places=4, read_only=True
    )
    currency = serializers.CharField(read_only=True)
    valid_from = serializers.DateField(read_only=True)
    valid_to = serializers.DateField(read_only=True, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)


class PreviewOptionSerializer(serializers.Serializer[Any]):
    """One class, its fare, and **which rung answered**.

    `step` is what makes this a diagnostic rather than a second price display.
    A fare that fell through to the country default when a corridor was
    expected is a misconfiguration indistinguishable from a correct answer
    without it — which is the whole reason §27.11 asks for the tool.
    """

    vehicle_class = serializers.CharField(read_only=True)
    seats = serializers.IntegerField(read_only=True)
    luggage = serializers.IntegerField(read_only=True)
    price = serializers.DictField(read_only=True)
    breakdown = serializers.DictField(read_only=True)
    matched_kind = serializers.CharField(read_only=True)
    matched_rule = serializers.UUIDField(read_only=True)
    matched_step = serializers.IntegerField(read_only=True)


class QuotePreviewResultSerializer(serializers.Serializer[Any]):
    origin = serializers.CharField(read_only=True)
    target = serializers.CharField(read_only=True)
    options = PreviewOptionSerializer(many=True, read_only=True)


# -- §27.7 providers ----------------------------------------------------------


class ProviderWriteSerializer(StrictSerializer):
    """§7.5.3's writable columns, as the console submits them.

    `region` is a slug, resolved by the service (ADR 0012). There is no
    `verify_status`: a provider moves through §26.2's states only by the status
    endpoint, which audits every step, and a field here would be a second door
    that audits none. `provider_type` is accepted on create and refused on
    update by the repository, because a type change would put every listing the
    provider owns in breach of §7.5.3 at once.
    """

    legal_name = serializers.CharField(max_length=200, required=False)
    trading_name = serializers.CharField(max_length=200, required=False)
    provider_type = serializers.ChoiceField(
        choices=["TRANSPORT", "ACCOMMODATION", "ACTIVITY"], required=False
    )
    contact_email = serializers.EmailField(required=False)
    contact_phone = serializers.CharField(max_length=20, required=False)
    region = serializers.CharField(required=False)
    payout_account_ref = serializers.CharField(max_length=120, required=False, allow_null=True)
    payout_currency = serializers.CharField(max_length=3, required=False)


class ProviderReadSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="public_id", read_only=True)
    legal_name = serializers.CharField(read_only=True)
    trading_name = serializers.CharField(read_only=True)
    provider_type = serializers.CharField(read_only=True)
    contact_email = serializers.CharField(read_only=True)
    contact_phone = serializers.CharField(read_only=True)
    region = serializers.CharField(read_only=True)
    verify_status = serializers.CharField(read_only=True)
    verified_at = serializers.DateTimeField(read_only=True, allow_null=True)
    is_sellable = serializers.BooleanField(read_only=True)
    payout_account_ref = serializers.CharField(read_only=True, allow_null=True)
    payout_currency = serializers.CharField(read_only=True)
    rating_avg = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    rating_count = serializers.IntegerField(read_only=True)


class ProviderStatusSerializer(StrictSerializer):
    """§26.2's decision. A reason is required to reject or suspend.

    §26.2 shows a rejected provider its "reasons", and a suspension is the
    most consequential thing an administrator can do to someone the platform
    pays; neither is acceptable as an unexplained row in the audit log.
    """

    status = serializers.ChoiceField(
        choices=["SUBMITTED", "UNDER_REVIEW", "VERIFIED", "REJECTED", "SUSPENDED"]
    )
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["status"] in {"REJECTED", "SUSPENDED"} and not attrs.get("reason", "").strip():
            raise serializers.ValidationError({"reason": "A reason is required."})
        return attrs


class ProviderStatusResultSerializer(serializers.Serializer[Any]):
    before = serializers.CharField(read_only=True)
    steps = serializers.ListField(child=serializers.CharField(), read_only=True)
    provider = ProviderReadSerializer(read_only=True)


class ActivityProviderSerializer(StrictSerializer):
    """The provider that sells an activity, by its public id."""

    provider = serializers.UUIDField()
