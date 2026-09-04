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

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.catalogue import services as catalogue
from apps.common.errors import ValidationError as PlatformValidationError
from apps.common.serializers import StrictSerializer
from apps.inventory.models import DepartureStatus

__all__ = [
    "DepartureQuerySerializer",
    "DepartureSerializer",
    "ProviderDepartureSerializer",
    "DepartureEditSerializer",
]

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


# ---------------------------------------------------------------------------
# The §26.5 calendar — an operator's view of the same rows
# ---------------------------------------------------------------------------


class ProviderDepartureSerializer(serializers.Serializer[Any]):
    """One departure with all three counters — §26.5's grid cell.

    The public `DepartureSerializer` above publishes `remaining` and refuses to
    publish the split. This one publishes it, because the split is the whole
    point of an operator's calendar: eight taken seats of which four are holds
    is a boat being booked, and eight of which four are sales with a hold
    expiring in ten minutes is a boat about to have a free seat. A provider
    deciding whether to cancel needs to tell those apart; a tourist reading the
    same number would watch availability move for no visible reason.

    Two serializers rather than one with a flag, so widening the tourist-facing
    payload stays something somebody has to do deliberately.
    """

    public_id = serializers.UUIDField(read_only=True)
    departs_at = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    capacity_total = serializers.IntegerField(read_only=True)
    capacity_held = serializers.IntegerField(read_only=True)
    capacity_sold = serializers.IntegerField(read_only=True)
    remaining = serializers.IntegerField(read_only=True)
    price_override = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, allow_null=True
    )


class DepartureEditSerializer(StrictSerializer):
    """§26.5's bulk submission: a window, an optional weekday mask, and the ops.

    *"Bulk editing supports a date range, a weekday mask, and set-availability /
    set-rate / open / close operations in one submission."*

    **`days`, not a bitmask**, matching the schedule console: a provider closing
    Sundays types "sun", and a form that took `64` would be one nobody can
    review. `catalogue.services.weekday_mask` is the same conversion the
    schedule form and the seed file use, so all three agree about which bit is
    Monday — and it is reached through the service interface because contract
    `private-catalogue` forbids this module the domain one.

    **An empty submission is refused.** A body naming a window and no operation
    would lock a month of rows, change none of them and answer 200 with a count
    of zero — indistinguishable from a window that matched nothing.

    **`clear_price` exists because `null` is ambiguous.** `price_override: null`
    has to mean "leave it alone", or every capacity edit would silently drop a
    special rate. Removing one is its own flag, and asking for both at once is
    a contradiction rather than a precedence rule to remember.
    """

    date_from = serializers.DateField()
    to = serializers.DateField()
    days = serializers.ListField(
        child=serializers.CharField(max_length=16), allow_empty=False, required=False
    )
    #: §26.5's "set availability". Zero is legal and is how a provider closes a
    #: date that nobody has booked; BR-023 is what stops it on a date somebody
    #: has.
    capacity_total = serializers.IntegerField(min_value=0, required=False)
    #: §26.5's "set rate". §7.2: an amount is a `Decimal`, and this one carries
    #: no currency of its own because a departure is priced in the activity's.
    price_override = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0"), required=False
    )
    clear_price = serializers.BooleanField(required=False, default=False)
    #: §26.5's "open / close", plus the cancellation that is neither. FULL is
    #: absent deliberately: it is arithmetic, not a decision, and letting an
    #: operator assert it would put a status on a row with seats left.
    status = serializers.ChoiceField(
        choices=[
            DepartureStatus.OPEN,
            DepartureStatus.CLOSED,
            DepartureStatus.CANCELLED,
        ],
        required=False,
    )

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        """`from` is a Python keyword, so the field cannot be called that.

        The same rename `DepartureQuerySerializer` does, for the same reason:
        the URL and the body should say `from` because that is what §26.5's
        range is called everywhere else in this API.
        """
        if hasattr(data, "dict"):
            data = data.dict()
        data = dict(data)
        if "from" in data:
            data["date_from"] = data.pop("from")
        validated: dict[str, Any] = super().to_internal_value(data)
        return validated

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        data = dict(attrs)
        since, until = data["date_from"], data["to"]
        if until < since:
            raise serializers.ValidationError({"to": "`to` cannot precede `from`."})
        if (until - since).days > MAX_WINDOW_DAYS:
            raise serializers.ValidationError(
                {"to": f"A window may not exceed {MAX_WINDOW_DAYS} days."}
            )

        days = data.pop("days", None)
        if days is not None:
            # Through `catalogue.services`, not `catalogue.domain`: contract
            # `private-catalogue` forbids the second, and the point of routing
            # through the first is that the schedule form, the seed file and
            # this calendar all get the same answer about which bit is Monday.
            try:
                data["weekday_mask"] = catalogue.weekday_mask(days)
            except PlatformValidationError as exc:
                raise serializers.ValidationError({"days": exc.message}) from exc

        if data.get("clear_price") and "price_override" in data:
            raise serializers.ValidationError(
                {"clear_price": "Cannot both set and clear the price override."}
            )

        operations = {"capacity_total", "price_override", "status"} & set(data)
        if not operations and not data.get("clear_price"):
            raise serializers.ValidationError(
                "An edit must set a capacity, a price or a status; a window alone changes nothing."
            )
        return data
