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

# Moved to `common` in Phase 5, and re-exported here so this module keeps
# naming what `trip`'s views use. §9.4.5's quote is `booking`'s use case
# (ADR 0022) and needs the same gate, and `apps.trip.permissions` is private
# to `trip` under §6.5 rule 1 — so the definition had to move somewhere both
# could reach. A copy in `booking` would be a second answer to "who is a
# tourist", and the one that drifts is always the copy.
from apps.common.permissions import IsTourist, tourist_id_of

__all__ = ["IsTourist", "tourist_id_of"]
