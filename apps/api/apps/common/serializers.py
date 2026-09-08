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

from typing import Any

from rest_framework import serializers

__all__ = ["StrictSerializer", "MoneySerializer"]


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
