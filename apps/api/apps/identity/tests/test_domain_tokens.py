"""Tests for refresh rotation and reuse detection — SRS §30.2.

The replay case gets its own class because it is the whole point of the
mechanism: everything else here is bookkeeping around it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from apps.identity.domain.tokens import (
    FamilyAction,
    RefusalReason,
    TokenView,
    evaluate_refresh,
)

T0 = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)
FAMILY = uuid.UUID(int=42)


def token(
    *,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    superseded_by: uuid.UUID | None = None,
) -> TokenView:
    return TokenView(
        jti=uuid.uuid4(),
        family_id=FAMILY,
        user_id=7,
        expires_at=expires_at or T0 + timedelta(days=30),
        revoked_at=revoked_at,
        superseded_by=superseded_by,
    )


class TestTheHappyPath:
    def test_a_live_token_rotates(self) -> None:
        d = evaluate_refresh(token(), now=T0)
        assert d.action is FamilyAction.ROTATE
        assert d.is_rotation
        assert d.reason is None
        assert d.family_id == FAMILY

    def test_rotation_does_not_alert_the_owner(self) -> None:
        assert not evaluate_refresh(token(), now=T0).alert_owner

    def test_a_token_one_second_from_expiry_still_rotates(self) -> None:
        t = token(expires_at=T0 + timedelta(seconds=1))
        assert evaluate_refresh(t, now=T0).is_rotation


class TestReplayRevokesTheFamily:
    """§30.2: "a replayed refresh token revokes the whole family and alerts
    the user"."""

    def test_a_superseded_token_revokes_the_family(self) -> None:
        d = evaluate_refresh(token(superseded_by=uuid.uuid4()), now=T0)
        assert d.action is FamilyAction.REVOKE_FAMILY
        assert d.reason is RefusalReason.SUPERSEDED
        assert d.family_id == FAMILY

    def test_replay_alerts_the_owner(self) -> None:
        """The only signal the user gets that their session was stolen."""
        assert evaluate_refresh(token(superseded_by=uuid.uuid4()), now=T0).alert_owner

    def test_replay_is_detected_even_while_the_token_is_still_valid(self) -> None:
        """The attacker's copy has not expired — that is why it is dangerous."""
        t = token(expires_at=T0 + timedelta(days=29), superseded_by=uuid.uuid4())
        d = evaluate_refresh(t, now=T0)
        assert d.action is FamilyAction.REVOKE_FAMILY
        assert d.reason is RefusalReason.SUPERSEDED

    def test_supersession_outranks_expiry(self) -> None:
        """A stolen-and-replayed token that also lapsed must still alert."""
        t = token(expires_at=T0 - timedelta(days=1), superseded_by=uuid.uuid4())
        d = evaluate_refresh(t, now=T0)
        assert d.reason is RefusalReason.SUPERSEDED
        assert d.alert_owner

    def test_the_full_theft_sequence(self) -> None:
        """Attacker steals token A, user rotates A->B, attacker presents A.

        Whichever party presents the superseded token, the family dies and
        the owner is told — the server cannot tell victim from thief, and
        guessing would be worse than revoking.
        """
        a = token()
        rotation = evaluate_refresh(a, now=T0)
        assert rotation.is_rotation

        # The service supersedes A when it issues B.
        a_after = TokenView(
            jti=a.jti,
            family_id=a.family_id,
            user_id=a.user_id,
            expires_at=a.expires_at,
            superseded_by=uuid.uuid4(),
        )

        replay = evaluate_refresh(a_after, now=T0 + timedelta(seconds=1))
        assert replay.action is FamilyAction.REVOKE_FAMILY
        assert replay.reason is RefusalReason.SUPERSEDED
        assert replay.alert_owner
        assert replay.family_id == FAMILY


class TestRevokedTokens:
    def test_an_explicitly_revoked_token_revokes_the_family(self) -> None:
        d = evaluate_refresh(token(revoked_at=T0 - timedelta(minutes=1)), now=T0)
        assert d.action is FamilyAction.REVOKE_FAMILY
        assert d.reason is RefusalReason.REVOKED

    def test_presenting_a_revoked_token_alerts_the_owner(self) -> None:
        """After logout, a token still being presented is a live credential
        someone else is holding."""
        assert evaluate_refresh(token(revoked_at=T0), now=T0).alert_owner


class TestExpiry:
    def test_an_expired_token_revokes_the_family(self) -> None:
        d = evaluate_refresh(token(expires_at=T0 - timedelta(seconds=1)), now=T0)
        assert d.action is FamilyAction.REVOKE_FAMILY
        assert d.reason is RefusalReason.EXPIRED

    def test_expiry_at_exactly_the_boundary_is_refused(self) -> None:
        assert not evaluate_refresh(token(expires_at=T0), now=T0).is_rotation

    def test_expiry_does_not_alert_the_owner(self) -> None:
        """Not the user's fault — a re-login, not an alarming email."""
        assert not evaluate_refresh(token(expires_at=T0 - timedelta(days=1)), now=T0).alert_owner


class TestUnknownTokens:
    def test_an_unknown_token_is_refused(self) -> None:
        d = evaluate_refresh(None, now=T0)
        assert d.action is FamilyAction.REVOKE_FAMILY
        assert d.reason is RefusalReason.UNKNOWN

    def test_an_unknown_token_names_no_family(self) -> None:
        """There is nothing to revoke and nobody to alert — the caller
        records it as an ordinary authentication failure."""
        d = evaluate_refresh(None, now=T0)
        assert d.family_id is None
        assert not d.alert_owner


class TestOnlyTheHappyPathRotates:
    @pytest.mark.parametrize(
        "bad",
        [
            token(superseded_by=uuid.uuid4()),
            token(revoked_at=T0),
            token(expires_at=T0 - timedelta(seconds=1)),
            None,
        ],
    )
    def test_everything_else_revokes(self, bad: TokenView | None) -> None:
        assert evaluate_refresh(bad, now=T0).action is FamilyAction.REVOKE_FAMILY

    def test_a_decision_is_immutable(self) -> None:
        d = evaluate_refresh(token(), now=T0)
        with pytest.raises(AttributeError):
            d.action = FamilyAction.REVOKE_FAMILY  # type: ignore[misc]
