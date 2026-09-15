"""Data-access layer (SRS §8.2 layer 4). Read queries.

The only door into the booking table for a request. §30.3: ownership is part of
the query, so a booking the caller may not see is indistinguishable from one
that does not exist — a 404, never a 403.
"""

from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from apps.booking.models import Booking
from apps.common.authz import Principal, Resource
from apps.common.scoping import scoped

__all__ = ["visible_to", "one_visible_to"]


def visible_to(principal: Principal | None, *, write: bool = False) -> QuerySet[Booking]:
    """API-05's "scoped by role", through the one function that builds the filter."""
    return scoped(Booking.objects.all(), principal, Resource.BOOKING, write=write)


def one_visible_to(
    principal: Principal | None, public_id: UUID, *, write: bool = False
) -> Booking | None:
    return visible_to(principal, write=write).filter(public_id=public_id).first()
