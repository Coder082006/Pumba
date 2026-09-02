"""inventory module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). Request and response shapes.

The SRS gives `GET /activities/{id}/departures?from&to&pax` in the §9.3.2
endpoint table and SD-06's `200 [departures, remaining]`, and nowhere gives the
payload. The shape below is derived from §9.4.3 — the accommodation availability
endpoint that ADR 0013 removed — because it is the only fully specified
availability response the document ever contained, and departing from its
structure for the one that survives would leave v2 with two.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.common.serializers import StrictSerializer

__all__ = ["DepartureQuerySerializer", "DepartureSerializer"]

#: A window wider than this is a client asking for the whole calendar. §16.2's
#: horizon is 180 days and the tourist-facing screen shows thirty; the cap is
#: about what one request may cost, not about what a departure is, which is why
#: it lives here rather than in `system_setting`.
MAX_WINDOW_DAYS = 180


class DepartureQuerySerializer(StrictSerializer):
    """`?from=&to=&pax=`.

    Strict, so `?date=` is a 422 naming the parameter rather than a full
    unfiltered 200 — the same reason `_ListingQuerySerializer` is.
    """

    #: Dates rather than instants. A tourist picks days on a calendar, and a
    #: client sending an instant would be asserting a timezone it does not
    #: own — the departure's zone is the destination's, and resolving one
    #: against the other belongs on the server.
    date_from = serializers.DateField(required=False)
    to = serializers.DateField(required=False)
    pax = serializers.IntegerField(required=False, min_value=1, max_value=99)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        # `from` is a Python keyword, so the field cannot be called that and
        # the query parameter has to be. Renaming on the way in keeps the URL
        # the one §9.3.2 publishes while the field stays declarable.
        if hasattr(data, "dict"):
            data = data.dict()
        data = dict(data)
        if "from" in data:
            data["date_from"] = data.pop("from")
        validated: dict[str, Any] = super().to_internal_value(data)
        return validated

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        since = attrs.get("date_from")
        until = attrs.get("to")
        if since and until:
            if until < since:
                raise serializers.ValidationError({"to": "`to` cannot precede `from`."})
            if (until - since).days > MAX_WINDOW_DAYS:
                raise serializers.ValidationError(
                    {"to": f"A window may not exceed {MAX_WINDOW_DAYS} days."}
                )
        return attrs


class DepartureSerializer(serializers.Serializer[Any]):
    """One departure — §7.5.9's row as §24.10 renders it.

    **`remaining`, not the three counters.** Publishing `capacity_held` would
    tell a tourist how many seats somebody else is midway through paying for: a
    number that is nobody's business, that changes with nothing happening, and
    that reads as availability disappearing for no reason.

    **`basis` is always present.** §17.1 I3 and §8.10: this figure may be stale
    and may never confirm a booking. A payload that said so only sometimes
    would be one a client learns to ignore.
    """

    public_id = serializers.UUIDField(read_only=True)
    departs_at = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    remaining = serializers.IntegerField(read_only=True)
    basis = serializers.CharField(read_only=True)
    price_override = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, allow_null=True
    )
    #: Null when the request named no party size, and when the departure is
    #: bookable by the party it did name. §24.10 renders "sold out" and "too
    #: late" differently, and both differently from a row somebody may take.
    unbookable = serializers.CharField(read_only=True, allow_null=True)
    is_bookable = serializers.BooleanField(read_only=True)
