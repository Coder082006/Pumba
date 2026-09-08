"""Serializer behaviour shared by every module — SRS §30.6.

    §30.6: "unknown fields rejected rather than ignored"; "mass assignment is
    prevented by explicit serializer field lists".

`StrictSerializer` began in `identity` and lives here because `catalogue`
needs the same rule and may not import `identity` (§6.4: `catalogue` depends
on `location` alone). That is the shared-kernel case ADR 0005 describes: a
control applied identically by every module belongs in `common`, not in
whichever module happened to need it first.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.presentment import display_of

__all__ = ["StrictSerializer", "MoneySerializer", "DisplayMoneyField"]


class StrictSerializer(serializers.Serializer[Any]):
    """Rejects unknown fields — SRS §30.6.

    DRF ignores them by default, which turns a client's typo into silence and
    lets a renamed field keep "working" while doing nothing.

    It is also half of the write path's mass-assignment defence. The other
    half is `apps.catalogue.repositories._WRITABLE`, and the duplication is
    deliberate: this one gives the administrator a 422 naming the field they
    got wrong, and that one holds even for a caller that never passed through
    a serializer — the seed loader, a management command, a console shell.
    """

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {field: "Unrecognised field." for field in sorted(unknown)}
                )
        return super().to_internal_value(data)


class MoneySerializer(serializers.Serializer[Any]):
    """`apps.common.money.Money` on the wire — SRS §9.1, §7.2.

    `{"amount": "38.00", "currency": "USD"}`, exactly as §9.4.4 writes it.

    **The amount is a string.** §18.5 prohibits float anywhere on the pricing
    path, and a JSON number is a float in every mainstream client: JavaScript
    parses `38.00` into an IEEE double and `0.1 + 0.2` stops equalling `0.3`
    somewhere between here and a receipt. A decimal string survives the trip
    and is what `Money.parse` reads back.

    **The currency is never optional.** §7.2: "Every money column is
    accompanied by a currency CHAR(3) column. Never store money without its
    currency." The wire keeps the same rule, because an amount that arrives
    without one is a number a client has to guess the meaning of — and in a
    platform that prices in TZS and shows in EUR, the guess is wrong often
    enough to matter.
    """

    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    currency = serializers.CharField(max_length=3, read_only=True)

    #: What the tourist asked to see it in — §9.1's `X-Currency`, §24.1's
    #: chooser. **Absent, not null**, when nothing was converted: no currency
    #: was asked for, the price is already in it, or no rate exists for the
    #: pair. A null would invite a client to print an empty string where a
    #: price should be; a missing key makes the branch obvious.
    display = serializers.SerializerMethodField()

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "amount": {"type": "string"},
                "currency": {"type": "string"},
                "rate": {"type": "string"},
                "as_of": {"type": "string", "format": "date-time"},
                "source": {"type": "string"},
            },
        }
    )
    def get_display(self, obj: Any) -> dict[str, Any] | None:
        """The converted figure, with the evidence of where it came from.

        `rate`, `as_of` and `source` travel with the amount because §20.6 says
        there are no untraceable conversions, and because §24.11 shows the
        tourist a figure they are entitled to know the provenance of. A
        converted number with no rate beside it is indistinguishable from a
        price, which is the confusion ADR 0024 exists to prevent.
        """
        converted = display_of(_amount_of(obj), _currency_of(obj))
        if converted is None:
            return None
        return {
            "amount": str(converted.amount),
            "currency": converted.currency,
            "rate": str(converted.rate.rate),
            "as_of": converted.rate.as_of,
            "source": converted.rate.source,
        }


def _amount_of(obj: Any) -> Decimal | None:
    value = _read(obj, "amount")
    return None if value is None else Decimal(str(value))


def _currency_of(obj: Any) -> str | None:
    value = _read(obj, "currency")
    return None if value is None else str(value)


@extend_schema_field(
    {
        "type": "object",
        "nullable": True,
        "properties": {
            "amount": {"type": "string"},
            "currency": {"type": "string"},
            "rate": {"type": "string"},
            "as_of": {"type": "string", "format": "date-time"},
            "source": {"type": "string"},
        },
        # All five, or the whole object is null. A generated client whose every
        # field was optional would make a caller narrow five times to print one
        # figure, and §20.6's traceability would be optional with it — the rate
        # is not a nicety, it is what makes the number readable as a conversion
        # rather than as a price.
        "required": ["amount", "currency", "rate", "as_of", "source"],
    }
)
class DisplayMoneyField(serializers.Field[Any, Any, Any, Any]):
    """A converted figure beside a price whose shape cannot be changed.

    `MoneySerializer` nests `{amount, currency, display}` and is what new
    payloads use. The trip, the itinerary item and the catalogue listing
    predate it and publish flat `amount` + `currency` columns that clients are
    already reading; restructuring those would break every one of them to add a
    field none of them requires. So the converted figure arrives as a sibling —
    `total_amount` keeps its meaning, `total_amount_display` is new, and a
    client that ignores it renders exactly what it rendered yesterday.

    **Null when nothing was converted**, which is three situations that look
    the same on screen and should: no currency was asked for, the price is
    already in it, or no rate exists for the pair. Unlike `MoneySerializer`'s
    nested key this one is nullable rather than absent, because a declared
    field that vanished from some rows and not others would be harder for a
    typed client to consume than one that is sometimes null.
    """

    def __init__(self, *, amount_field: str, currency_field: str = "currency", **kwargs: Any):
        kwargs.setdefault("read_only", True)
        # `source="*"` hands `to_representation` the whole object, which is
        # what lets one field read two attributes — a money amount is never
        # meaningful without its currency (§7.2).
        kwargs.setdefault("source", "*")
        self.amount_field = amount_field
        self.currency_field = currency_field
        super().__init__(**kwargs)

    def to_representation(self, value: Any) -> dict[str, Any] | None:
        converted = display_of(_read(value, self.amount_field), _read(value, self.currency_field))
        if converted is None:
            return None
        return {
            "amount": str(converted.amount),
            "currency": converted.currency,
            "rate": str(converted.rate.rate),
            "as_of": converted.rate.as_of.isoformat(),
            "source": converted.rate.source,
        }


def _read(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
