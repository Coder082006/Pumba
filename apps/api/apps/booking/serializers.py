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

__all__ = ["QuoteSerializer"]


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
