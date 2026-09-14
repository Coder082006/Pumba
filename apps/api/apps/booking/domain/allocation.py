"""Sharing a trip-level amount across its bookings — ADR 0025 decision 3.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

The service fee and tax are computed once, on the trip's subtotal (§10.7), and a
booking needs its own share: §20.9 retains or refunds "the pro-rata service fee
for that component", and a refund decided per booking has to know what that
booking's share was *when it was sold*, not what the rate says later.

**Largest remainder, to the cent.** Pro-rata shares of a two-place amount rarely
land on two places. Rounding each share independently makes the shares sum to
a cent or two more or less than the trip's figure, and that difference would
sit in no account at all — a fee charged to the tourist that no booking owns,
or a booking refunding a cent nobody paid. So every share is floored, and the
cents left over go to the largest fractional remainders, ties broken by
position. The shares then sum to the total exactly, by construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal

__all__ = ["AllocationError", "allocate"]

_CENT = Decimal("0.01")


class AllocationError(ValueError):
    """Inputs no allocation can honour."""


def allocate(total: Decimal, weights: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Split `total` across `weights` pro rata, summing to `total` exactly.

    `weights` are the components' gross amounts. An all-zero weighting — every
    component free — divides the total evenly instead, because a fee on a trip
    of free components still has to belong to somebody.
    """
    if total < 0:
        raise AllocationError("a negative amount cannot be allocated")
    if any(weight < 0 for weight in weights):
        raise AllocationError("a weight may not be negative")
    if total.quantize(_CENT) != total:
        raise AllocationError(f"{total} has more than two decimal places")
    if not weights:
        if total:
            raise AllocationError("a non-zero amount needs somebody to belong to")
        return ()

    basis = sum(weights, Decimal(0))
    shares = [Decimal(1) for _ in weights] if basis == 0 else list(weights)
    whole = sum(shares, Decimal(0))

    exact = [total * share / whole for share in shares]
    floored = [value.quantize(_CENT, rounding=ROUND_FLOOR) for value in exact]
    leftover = int(((total - sum(floored, Decimal(0))) / _CENT).to_integral_value())

    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floored[i]), i))
    for index in order[:leftover]:
        floored[index] += _CENT
    return tuple(floored)
