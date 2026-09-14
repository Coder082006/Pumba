"""The provider verification state machine — SRS §26.2, §28.4, Appendix A.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§26.2 draws onboarding as a line with one fork:

    DRAFT --submit--> SUBMITTED --review--> UNDER_REVIEW --+--> VERIFIED <--+
                         ^                                 |       |        |
                         |                                 |    suspend  reinstate
                         +---------- resubmit -------------+       v        |
                                                 REJECTED <-+   SUSPENDED --+

**VERIFIED is the only state in which a provider can be sold.** BR-037: "a
booking's provider must be VERIFIED and unsuspended at the moment of
confirmation". SUSPENDED is a separate state rather than a flag on VERIFIED
precisely so that sentence is one comparison, not two.

**No state is terminal.** A rejected provider may resubmit (§26.2: "reasons
shown, resubmit") and a suspended one may be reinstated. Nothing about a
provider ends; it is either sellable or it is not.

**There is no DRAFT → VERIFIED edge, and the administrator's "verify" action
does not create one.** In Phase 7 there is no provider login, so an
administrator drives every step — but `path_to_verified` walks the edges §26.2
draws and the service audits each one. An administrator who could jump straight
to VERIFIED would leave a history that says a provider was never submitted or
reviewed, which is the one thing a verification trail exists to show (ADR 0025).
"""

from __future__ import annotations

from enum import StrEnum

from apps.common.state_machine import StateMachine, Transition

__all__ = [
    "ProviderType",
    "VerifyState",
    "VERIFY_MACHINE",
    "ListingKind",
    "is_sellable",
    "path_to_verified",
    "may_own",
]


class ProviderType(StrEnum):
    """§7.5.3's `provider_type`. Mirrors `models.ProviderTypeChoice`."""

    TRANSPORT = "TRANSPORT"
    ACCOMMODATION = "ACCOMMODATION"
    ACTIVITY = "ACTIVITY"


class VerifyState(StrEnum):
    """§7.5.3's `verify_status`. Mirrors `models.VerifyStatus`.

    Written twice because this module may not import Django; the lifecycle
    tests compare the two sets so a state added to one cannot be missing from
    the other.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


VERIFY_MACHINE: StateMachine[VerifyState] = StateMachine(
    name="provider_verification",
    initial=VerifyState.DRAFT,
    transitions=[
        Transition(VerifyState.DRAFT, VerifyState.SUBMITTED),
        Transition(VerifyState.SUBMITTED, VerifyState.UNDER_REVIEW),
        Transition(VerifyState.UNDER_REVIEW, VerifyState.VERIFIED),
        Transition(VerifyState.UNDER_REVIEW, VerifyState.REJECTED),
        Transition(VerifyState.REJECTED, VerifyState.SUBMITTED),
        Transition(VerifyState.VERIFIED, VerifyState.SUSPENDED),
        Transition(VerifyState.SUSPENDED, VerifyState.VERIFIED),
    ],
)


def is_sellable(state: VerifyState) -> bool:
    """BR-037, asked in one place.

    A set of one rather than `== VERIFIED` inline, for the reason
    `inventory.domain.lifecycle.LIVE_STATES` gives: basket creation, the
    confirmation routine and the admin console all ask it, and three spellings
    of one predicate is how they drift.
    """
    return state in _SELLABLE


_SELLABLE = frozenset({VerifyState.VERIFIED})


def path_to_verified(state: VerifyState) -> tuple[VerifyState, ...]:
    """The declared steps from `state` to VERIFIED, excluding `state` itself.

    Empty when already verified. Every hop is a declared edge — asserted below,
    so a change to the table that broke the walk fails loudly here rather than
    producing a path the machine would refuse halfway along.
    """
    route: tuple[VerifyState, ...] = _ROUTES[state]
    current = state
    for step in route:
        if not VERIFY_MACHINE.can(current, step):
            raise AssertionError(f"{current} -> {step} is not a declared edge")
        current = step
    return route


_ROUTES: dict[VerifyState, tuple[VerifyState, ...]] = {
    VerifyState.DRAFT: (VerifyState.SUBMITTED, VerifyState.UNDER_REVIEW, VerifyState.VERIFIED),
    VerifyState.SUBMITTED: (VerifyState.UNDER_REVIEW, VerifyState.VERIFIED),
    VerifyState.UNDER_REVIEW: (VerifyState.VERIFIED,),
    VerifyState.REJECTED: (
        VerifyState.SUBMITTED,
        VerifyState.UNDER_REVIEW,
        VerifyState.VERIFIED,
    ),
    VerifyState.SUSPENDED: (VerifyState.VERIFIED,),
    VerifyState.VERIFIED: (),
}


class ListingKind(StrEnum):
    """What a provider can be the seller of."""

    ACTIVITY = "ACTIVITY"
    TRANSFER = "TRANSFER"
    ACCOMMODATION = "ACCOMMODATION"


_OWNS: dict[ProviderType, ListingKind] = {
    ProviderType.ACTIVITY: ListingKind.ACTIVITY,
    ProviderType.TRANSPORT: ListingKind.TRANSFER,
    ProviderType.ACCOMMODATION: ListingKind.ACCOMMODATION,
}


def may_own(provider_type: ProviderType, kind: ListingKind) -> bool:
    """§7.5.3: "a provider may not own listings of a type inconsistent with
    provider_type".

    One kind per type. A tour operator that also runs cars is two providers,
    because the two are paid, verified and held to account differently —
    §26.4 makes transfer pricing platform-managed and activity pricing the
    provider's own.
    """
    return _OWNS[provider_type] is kind
