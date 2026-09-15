"""Interface layer (SRS §8.2 layer 1). Request and response shapes.

§9.4.7 gives the request body as `{trip_id, method, currency, return_url}` and
the response as a method-specific action object. Both are here; nothing else
in this module knows what JSON looks like.

**`amount` is accepted and never used to charge.** §9.4.7's body does not
include one, but a client can send anything, and TC-073 requires that a
submitted 8.34 against a basket of 834.75 charges 834.75 and records the
attempt. A serializer that rejected the field would turn a tampering attempt
into a 422 — indistinguishable, in the log, from a client with a bug.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.common.serializers import StrictSerializer

__all__ = [
    "PaymentIntentSerializer",
    "PaymentActionSerializer",
    "PaymentSerializer",
    "PaymentMethodSerializer",
]


class PaymentIntentSerializer(StrictSerializer):
    """§9.4.7's request."""

    trip_id = serializers.UUIDField()
    method = serializers.ChoiceField(choices=["CARD", "MOBILE_MONEY"], default="CARD")
    #: Accepted for symmetry with §9.4.7 and ignored: the trip's currency is
    #: what it was priced and reserved in (BR-016, ADR 0024), and a payment in
    #: another currency would be a different price.
    currency = serializers.CharField(max_length=3, required=False)
    return_url = serializers.URLField(required=False, allow_blank=True)
    #: Never charged. See the module docstring.
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )


class PaymentActionSerializer(serializers.Serializer[Any]):
    type = serializers.CharField(read_only=True)
    payload = serializers.DictField(child=serializers.CharField(), read_only=True)


class PaymentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(source="public_id", read_only=True)
    trip_id = serializers.UUIDField(source="trip_public_id", read_only=True)
    status = serializers.CharField(read_only=True)
    method = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    action = PaymentActionSerializer(read_only=True, allow_null=True)
    failure_code = serializers.CharField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    captured_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True, allow_null=True)


class PaymentMethodSerializer(serializers.Serializer[Any]):
    """`GET /payments/methods` — what this tourist may actually use.

    The human-readable name is `display_name` rather than `label`: DRF's own
    `Field.label` is a different thing (the form label of the field itself),
    and declaring one here shadows it.
    """

    method = serializers.CharField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    currencies = serializers.ListField(child=serializers.CharField(), read_only=True)
    available = serializers.BooleanField(read_only=True)
    unavailable_reason = serializers.CharField(read_only=True, allow_blank=True)
