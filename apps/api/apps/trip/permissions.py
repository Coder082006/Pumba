"""trip module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). Role checks, then ownership checks.

**Ownership is deliberately not a permission class**, and that is the whole
design of this file.

A DRF permission class that loaded the trip and compared owners would return
`403 PERMISSION_DENIED` for somebody else's trip — and §30.3 requires `404`, so
that absence and inaccessibility are indistinguishable. A 403 confirms the trip
exists, which is exactly the disclosure the rule exists to prevent, and it does
so for every id an attacker cares to try.

So ownership is the `tourist_id` argument that every function in `services`
takes, and it goes into the `WHERE` clause. The only thing this module decides
is whether the caller is a tourist at all.

`IsTourist` therefore refuses a principal with no `tourist_id` — an
administrator or a driver — with 403, and that is correct: the answer does not
depend on which trip they asked for, so it discloses nothing about any of them.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.common.authentication import principal_from_request

__all__ = ["IsTourist", "tourist_id_of"]


class IsTourist(BasePermission):
    """The principal has a tourist profile.

    Not "the principal owns this trip" — see the module docstring. This is a
    question about the caller alone, which is why answering it with 403
    discloses nothing.
    """

    message = "A tourist profile is required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        return principal is not None and principal.tourist_id is not None


def tourist_id_of(request: Request) -> int:
    """The caller's `tourist_profile.id`, for the service's `tourist_id`.

    `IsTourist` has already run, so both the principal and the id are present;
    the assertions state that rather than defending against it, because a view
    reachable without them would be a routing bug and silently defaulting to
    some other tourist is the worst possible recovery.
    """
    principal = principal_from_request(request)
    assert principal is not None, "IsTourist should have refused an anonymous request"
    assert principal.tourist_id is not None, "IsTourist should have refused a non-tourist"
    return principal.tourist_id
