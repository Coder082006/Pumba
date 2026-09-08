"""Interface layer (SRS §8.2 layer 1).

The wire shape for `GET /transport/vehicle-classes` (§9.3.4). It is the one
public transport read that names no `catalogue` row, which is why it is the
only one served from this module: a corridor names two destinations, and
rendering those as anything a client may see is a catalogue read `transport`
may not do. The corridor list and the quote are served by `trip` (ADR 0023).

Read-only, and every field is declared rather than derived from a model.
`drf-spectacular` publishes what it finds here into `packages/contracts`, and a
`ModelSerializer` would publish `id` and `deleted_at` the first time somebody
added a column — the §7.2 rule that sequential integers never leave the
database is not a rule a client should be trusted to ignore.
"""

from __future__ import annotations

from rest_framework import serializers

__all__ = ["VehicleClassSerializer"]


class VehicleClassSerializer(serializers.Serializer[object]):
    """§9.3.4: "Classes with capacity and indicative pricing".

    Capacity, and no price. A class does not have a price — a *route* priced
    for a class does — so a number here would be a quote nobody asked for and
    nobody could be held to. §24.16's cards get theirs from
    `POST /transport/quotes`, which knows the leg.
    """

    id = serializers.UUIDField(source="public_id", read_only=True)
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    seats = serializers.IntegerField(read_only=True)
    luggage_capacity = serializers.IntegerField(read_only=True)
    has_air_conditioning = serializers.BooleanField(read_only=True)
