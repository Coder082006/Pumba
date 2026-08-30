"""What the planner reports back — SRS §10.6.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2).

§10.6 fixes the shape: "Each finding carries {code, severity, message,
item_ids[], suggested_action} so the client can render an inline fix
affordance." That last clause is the reason `item_ids` and `suggested_action`
are required rather than optional. A finding the client cannot anchor against
an item is a banner the tourist has to act on by guessing, which is the
difference between "your day 3 has no way to get from the hotel to the boat"
and "this itinerary has errors".

`suggested_action` is a machine-readable code, not a sentence. The sentence is
the client's, so it can be translated (§23.4) and phrased for the surface it
appears on; the server says what kind of fix applies, and the client knows how
to offer it.

Severity is two-valued on purpose. §10.6: ERROR blocks quoting, WARNING is
advisory. A third level would immediately raise the question of which side of
the quote gate it sits on, and there is no third answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["Severity", "SuggestedAction", "Finding", "worst_severity"]


class Severity(StrEnum):
    """§10.6. ERROR blocks quoting; WARNING is advisory."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class SuggestedAction(StrEnum):
    """The fix affordance the client should offer.

    Named for what the tourist would *do*, not for what is wrong, because the
    client renders it as a button. `REMOVE_ITEM` and `RESCHEDULE_ITEM` look
    similar from the server and are entirely different offers on screen.
    """

    #: Nothing actionable in the client; the tourist is being told something.
    NONE = "NONE"
    RESCHEDULE_ITEM = "RESCHEDULE_ITEM"
    REMOVE_ITEM = "REMOVE_ITEM"
    ADD_STAY = "ADD_STAY"
    ADD_TRANSFER = "ADD_TRANSFER"
    EDIT_TRIP_DATES = "EDIT_TRIP_DATES"
    EDIT_PARTY = "EDIT_PARTY"
    EDIT_FLIGHT = "EDIT_FLIGHT"
    CONTACT_SUPPORT = "CONTACT_SUPPORT"


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the planner has to say about an itinerary.

    `item_ids` is a tuple rather than a list, so whatever collects a finding
    cannot append to it in passing. Empty is legitimate — VR-16's "no
    accommodation for one or more nights" is about items that are *absent*,
    and there is nothing to anchor it to.

    The dataclass is frozen, so no field can be reassigned. It is **not**
    hashable, because `context` is a mutable mapping: findings go into lists
    and into JSON, never into sets, and a tuple-of-pairs context that bought
    hashability would cost every call site its readability for a property
    nothing needs.
    """

    code: str
    severity: Severity
    message: str
    item_ids: tuple[int, ...] = ()
    suggested_action: SuggestedAction = SuggestedAction.NONE
    #: Free-form, machine-readable context for the client's message —
    #: `{"day_number": 3}`, `{"required_minutes": 45}`. Never prose: the prose
    #: is `message`, and the client's own translation uses these.
    context: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("a finding needs a code; the client keys its copy on it")
        if not self.message:
            raise ValueError(f"{self.code} needs a message")

    @property
    def blocks_quoting(self) -> bool:
        """§10.6: only an ERROR does."""
        return self.severity is Severity.ERROR


def worst_severity(findings: tuple[Finding, ...]) -> Severity | None:
    """The severity an itinerary should be summarised at, or None if clean.

    Deliberately not a count. §24.14's banner asks one question — may this be
    quoted — and the answer is decided by the worst finding, not by how many
    there are.
    """
    if any(f.severity is Severity.ERROR for f in findings):
        return Severity.ERROR
    if findings:
        return Severity.WARNING
    return None
