"""booking module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). The quote's response shape.

§9.4.5: *"Response 200 returns the full cost breakdown plus `quote_expires_at`
and a `quote_token` that must be presented at confirmation."*

**The trip itself is not embedded, and that is a boundary decision rather than
a shortcut.** The obvious response would nest `trip.serializers.TripSerializer`
— but `private-trip` forbids importing it, and rightly: §6.5 rule 1 makes a
module's `services` and `dto` its public surface and nothing else, because a
serializer is a rendering decision that must stay free to change without four
other modules re-rendering with it.

Restating those sixty fields here would be worse than the import. So the quote
answers with what a quote *is* — the totals, the token, the clock — and the
client re-reads `GET /trips/{id}` for the line-by-line breakdown. It has to
anyway: the trip is now `PRICED`, its items carry bound departures, and §24.14
re-renders after every change.

The figures below come off `trip.dto.TripDTO`, which *is* importable, so this
serializer reads a value object rather than duplicating a computation.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.common.serializers import DisplayMoneyField, StrictSerializer

__all__ = [
    "QuoteSerializer",
    "ConfirmSerializer",
    "BookingSerializer",
    "BasketSerializer",
    "CancellationSerializer",
    "TripCancellationSerializer",
    "BookingDetailSerializer",
    "CancelRequestSerializer",
    "DeclineRequestSerializer",
]


class QuoteSerializer(serializers.Serializer[Any]):
    """A priced, inventory-backed, time-boxed offer."""

    #: §9.4.5. Presented at confirmation, and a superseded quote's token must
    #: not be accepted — so it changes whenever the trip is re-quoted.
    quote_token = serializers.UUIDField(read_only=True)

    #: When the offer stops standing, and the same instant the seats behind it
    #: are released. §24.20 counts down to it.
    expires_at = serializers.DateTimeField(read_only=True)

    #: How many seats are held. Not in §9.4.5's response and included anyway:
    #: a tourist told "your seats are held for twenty minutes" is owed the
    #: number, and without it a quote that held nothing looks identical to one
    #: that held everything.
    held_seats = serializers.IntegerField(read_only=True)

    trip_public_id = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    subtotal_amount = serializers.SerializerMethodField()
    fee_amount = serializers.SerializerMethodField()
    tax_amount = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    def get_trip_public_id(self, obj: Any) -> str:
        return str(obj.trip.public_id)

    def get_status(self, obj: Any) -> str:
        return str(obj.trip.status)

    def get_currency(self, obj: Any) -> str:
        return str(obj.trip.currency)

    def get_subtotal_amount(self, obj: Any) -> str:
        return str(obj.trip.subtotal_amount)

    def get_fee_amount(self, obj: Any) -> str:
        return str(obj.trip.fee_amount)

    def get_tax_amount(self, obj: Any) -> str:
        return str(obj.trip.tax_amount)

    def get_total_amount(self, obj: Any) -> str:
        return str(obj.trip.total_amount)


class ConfirmSerializer(StrictSerializer):
    """§9.4.6's request: `{"quote_token": "…", "payment_method": "CARD"}`.

    `payment_method` is accepted and not yet acted on. Payment is Phase 8, and
    refusing a field §9.4.6 names would break a client written to the SRS; the
    basket it creates is the same whichever method follows.
    """

    quote_token = serializers.UUIDField()
    payment_method = serializers.ChoiceField(choices=["CARD", "MOBILE_MONEY"], required=False)


class BookingSerializer(serializers.Serializer[Any]):
    """One component booking. `id` is the `public_id` (§7.2)."""

    id = serializers.UUIDField(source="public_id", read_only=True)
    reference = serializers.CharField(read_only=True)
    booking_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)
    pax_count = serializers.IntegerField(read_only=True)
    gross_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    gross_amount_display = DisplayMoneyField(amount_field="gross_amount")
    fee_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)
    cancellation_policy_code = serializers.CharField(read_only=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    response_due_at = serializers.DateTimeField(read_only=True, allow_null=True)


class BasketSerializer(serializers.Serializer[Any]):
    """§9.4.6's 201: "the basket and the total payable"."""

    trip_public_id = serializers.UUIDField(read_only=True)
    trip_status = serializers.CharField(read_only=True)
    currency = serializers.CharField(read_only=True)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_amount_display = DisplayMoneyField(amount_field="total_amount")
    payment_expires_at = serializers.DateTimeField(read_only=True)
    bookings = BookingSerializer(many=True, read_only=True)


class CancellationSerializer(serializers.Serializer[Any]):
    """One component's refund, previewed or done — §20.9, BR-043."""

    booking = BookingSerializer(read_only=True)
    cancellable = serializers.BooleanField(read_only=True)
    policy_code = serializers.CharField(read_only=True)
    refund_percent = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    refund_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    refund_amount_display = DisplayMoneyField(amount_field="refund_amount")
    refund_of_price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    fee_refunded = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    tax_refunded = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(read_only=True)


class TripCancellationSerializer(serializers.Serializer[Any]):
    """§20.9's "itemised total". `status` and `cancelled_at` stay at the top
    level, where `POST /trips/{id}/cancel` has always put them."""

    public_id = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    cancelled_at = serializers.SerializerMethodField()
    currency = serializers.CharField(read_only=True)
    refund_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    refund_amount_display = DisplayMoneyField(amount_field="refund_amount")
    components = CancellationSerializer(many=True, read_only=True)

    def get_public_id(self, obj: Any) -> str:
        return str(obj.trip.public_id)

    def get_status(self, obj: Any) -> str:
        return str(obj.trip.status)

    def get_cancelled_at(self, obj: Any) -> str | None:
        stamp = obj.trip.cancelled_at
        return None if stamp is None else stamp.isoformat()


class HistorySerializer(serializers.Serializer[Any]):
    """BR-032: who moved it, from what, to what, and why."""

    from_status = serializers.CharField(read_only=True, allow_null=True)
    to_status = serializers.CharField(read_only=True)
    actor_role = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)
    occurred_at = serializers.DateTimeField(read_only=True)


class ProviderContactSerializer(serializers.Serializer[Any]):
    """§9.3.5: the detail carries "provider contact"."""

    name = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)


class BookingDetailSerializer(BookingSerializer):
    """`GET /bookings/{id}` — "Detail incl. status history and provider contact"."""

    provider = ProviderContactSerializer(read_only=True, allow_null=True)
    history = HistorySerializer(many=True, read_only=True)
    has_voucher = serializers.BooleanField(read_only=True)


class CancelRequestSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class DeclineRequestSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500)
