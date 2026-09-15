"""The words on a voucher — ADR 0026 decision 2.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

A voucher is read by a provider at a jetty and by a tourist on a phone with no
signal, so every figure on it is written out in words a person reads, from the
booking's own frozen facts. These functions turn those facts into that text,
and they are pure so the text is decided once and tested once — the renderer
only lays it out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

__all__ = ["policy_summary", "party_text", "money_text", "when_text"]


def policy_summary(tiers: Sequence[Mapping[str, object]]) -> str:
    """A policy snapshot's tiers as one sentence.

    `[{168, 100}, {48, 50}]` reads "Full refund more than 168 hours before the
    start; 50% refund more than 48 hours before; no refund after that." Hours,
    not days: a policy written in hours is enforced in hours, and rounding
    "168 hours" to "7 days" on the one document a tourist keeps would be the
    platform paraphrasing its own terms.
    """
    if not tiers:
        return "Non-refundable: no refund at any time."
    parts: list[str] = []
    for index, tier in enumerate(tiers):
        percent = Decimal(str(tier["refund_percent"]))
        amount = "Full refund" if percent == 100 else f"{percent.normalize():f}% refund"
        amount_text = amount if index == 0 else amount[0].lower() + amount[1:]
        suffix = " the start" if index == 0 else ""
        parts.append(f"{amount_text} more than {tier['hours_before']} hours before{suffix}")
    return "; ".join(parts) + "; no refund after that."


def party_text(*, adults: int, children: int) -> str:
    """ "2 adults, 1 child". A zero is left out rather than written as "0 children"."""
    words: list[str] = []
    if adults:
        words.append(f"{adults} adult{'s' if adults != 1 else ''}")
    if children:
        words.append(f"{children} child{'ren' if children != 1 else ''}")
    return ", ".join(words) or "No travellers"


def money_text(amount: Decimal, currency: str) -> str:
    """ "76,000.00 TZS" — grouped, two places, currency after (§7.2)."""
    return f"{amount.quantize(Decimal('0.01')):,.2f} {currency}"


def when_text(instant: datetime, zone: str) -> str:
    """The start in the destination's own time, with the zone named.

    §7.2 stores UTC and renders in the destination's timezone. The zone is
    printed because a tourist reading a voucher at home is in a different one,
    and a bare "16:45" is a time in nobody's clock.
    """
    local = instant.astimezone(ZoneInfo(zone))
    return f"{local:%a %d %b %Y, %H:%M} ({zone})"
