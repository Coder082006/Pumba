"""Refresh-token rotation and reuse detection — SRS §30.2.

    "Sessions are JWT-based: access token 15 minutes, refresh token 30 days
     with rotation and reuse detection — a replayed refresh token revokes the
     whole family and alerts the user."

A *family* is the chain of refresh tokens descending from one login. Rotation
issues a successor and marks its predecessor superseded, so at any moment
exactly one token in the family is live.

That invariant is what makes theft detectable. If an attacker steals a refresh
token and uses it, one of two things happens:

* the legitimate user refreshes afterwards and presents a token that is now
  superseded, or
* the attacker presents a token the legitimate user already exchanged.

Either way somebody presents a **superseded** token, and neither party can
tell which of them was robbed. So the only safe response is to revoke the
entire family and tell the account owner — §30.2 requires exactly that.

The consequence, stated so it is a choice rather than a surprise: a legitimate
user whose client races two refreshes will be logged out. That is the correct
trade. The alternative — tolerating one replay — is indistinguishable from
tolerating the attack.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

__all__ = ["FamilyAction", "RefusalReason", "TokenView", "RotationDecision", "evaluate_refresh"]


class FamilyAction(StrEnum):
    ROTATE = "ROTATE"
    """Issue a successor and supersede the presented token."""

    REVOKE_FAMILY = "REVOKE_FAMILY"
    """Kill every token descending from this login."""


class RefusalReason(StrEnum):
    UNKNOWN = "UNKNOWN"
    """No such token. Either forged, or from a family already purged."""

    SUPERSEDED = "SUPERSEDED"
    """Already exchanged. This is the replay signal."""

    REVOKED = "REVOKED"
    """Explicitly revoked — logout, or an earlier family revocation."""

    EXPIRED = "EXPIRED"
    """Past its 30 days."""


@dataclass(frozen=True, slots=True)
class TokenView:
    """What the repository loads for a presented refresh token. No ORM."""

    jti: UUID
    family_id: UUID
    user_id: int
    expires_at: datetime
    revoked_at: datetime | None = None
    superseded_by: UUID | None = None


@dataclass(frozen=True, slots=True)
class RotationDecision:
    action: FamilyAction
    reason: RefusalReason | None
    alert_owner: bool
    family_id: UUID | None

    @property
    def is_rotation(self) -> bool:
        return self.action is FamilyAction.ROTATE


def evaluate_refresh(token: TokenView | None, *, now: datetime) -> RotationDecision:
    """Decide what a presented refresh token earns.

    Only a token that is known, unrevoked, unexpired and not yet superseded
    rotates. Everything else revokes the family.

    Expiry is treated as a family revocation rather than a plain refusal on
    purpose: a token presented long after it lapsed is far more likely to have
    come from a stolen store than from a client that slept for a month, and
    revoking a family whose tokens are all expired anyway costs a legitimate
    user nothing.
    """
    if token is None:
        # No family to revoke — there is nothing to identify. The refusal is
        # still recorded by the caller as an authentication failure.
        return RotationDecision(
            action=FamilyAction.REVOKE_FAMILY,
            reason=RefusalReason.UNKNOWN,
            alert_owner=False,
            family_id=None,
        )

    if token.superseded_by is not None:
        # §30.2's replay case. Cannot tell victim from attacker, so revoke.
        return _revoke(token, RefusalReason.SUPERSEDED, alert_owner=True)

    if token.revoked_at is not None:
        return _revoke(token, RefusalReason.REVOKED, alert_owner=True)

    if now >= token.expires_at:
        # Not the user's fault, so no alarming email — just a re-login.
        return _revoke(token, RefusalReason.EXPIRED, alert_owner=False)

    return RotationDecision(
        action=FamilyAction.ROTATE,
        reason=None,
        alert_owner=False,
        family_id=token.family_id,
    )


def _revoke(token: TokenView, reason: RefusalReason, *, alert_owner: bool) -> RotationDecision:
    return RotationDecision(
        action=FamilyAction.REVOKE_FAMILY,
        reason=reason,
        alert_owner=alert_owner,
        family_id=token.family_id,
    )
