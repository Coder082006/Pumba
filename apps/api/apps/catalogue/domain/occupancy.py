"""Room occupancy — SRS BR-102, §14.1.

    BR-102  Occupancy of the selected room type must accommodate the assigned
            guests; excess parties must book multiple rooms

§14.1 is explicit that the Platform sells **room types, not individual rooms**,
and that the property allocates a physical room at check-in. So occupancy is a
per-room-type capacity question, and a party larger than one room's capacity is
not a rejection — it is a request for more rooms.

The naive form of this rule, `adults + children <= max_adults + max_children`,
is wrong in a way that produces a real complaint: a room for two adults and two
children accepts four adults, the property refuses them at the desk, and the
platform has taken the money. Adults and children are checked separately, and
children never overflow into adult capacity.

Children *do* count as adults for the purposes of the reverse question,
`rooms_required`, only in the sense that each room contributes its own
allowances — two rooms of (2 adults, 2 children) hold four adults and four
children, not four of any mix.
"""

from __future__ import annotations

import math

__all__ = ["party_fits", "rooms_required", "OccupancyError"]


class OccupancyError(ValueError):
    """A room type that cannot hold any party at all."""


def party_fits(*, max_adults: int, max_children: int, adults: int, children: int) -> bool:
    """Does one room of this type hold this party? BR-102.

    Adults and children are separate allowances. A room for 2 + 2 does not hold
    4 adults, however much the arithmetic wants it to.
    """
    _validate(max_adults=max_adults, max_children=max_children)
    if adults < 1:
        raise ValueError("a party must include at least one adult")
    if children < 0:
        raise ValueError("children cannot be negative")
    return adults <= max_adults and children <= max_children


def rooms_required(*, max_adults: int, max_children: int, adults: int, children: int) -> int:
    """How many rooms of this type this party needs. BR-102.

    Each room contributes its own adult and child allowances, and the answer is
    the larger of the two requirements — a party of 2 adults and 6 children in
    a 2+2 room needs three rooms for the children even though the adults fit in
    one.

    Children are not placed in rooms without an adult. The count is therefore
    also floored at the number of rooms the adults alone would occupy, which is
    already the case here since every room needs at least one adult only when
    it is used at all.
    """
    _validate(max_adults=max_adults, max_children=max_children)
    if adults < 1:
        raise ValueError("a party must include at least one adult")
    if children < 0:
        raise ValueError("children cannot be negative")

    if children and max_children == 0:
        # No number of rooms accommodates a child in a room type that takes
        # none. Returning a large integer would let the caller price a stay
        # the property will refuse at the desk.
        raise OccupancyError("this room type does not accept children")

    for_adults = math.ceil(adults / max_adults)
    for_children = math.ceil(children / max_children) if children else 0
    return max(1, for_adults, for_children)


def _validate(*, max_adults: int, max_children: int) -> None:
    if max_adults < 1:
        raise OccupancyError("a room type must hold at least one adult")
    if max_children < 0:
        raise OccupancyError("max_children cannot be negative")
