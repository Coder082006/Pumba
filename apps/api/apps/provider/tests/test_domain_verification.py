"""The provider verification machine — SRS §26.2, §28.4, BR-037.

Pure-domain tests: no database. The costly mistake here is a spare edge — a
provider reaching VERIFIED without passing review, which is a provider the
platform will take money on behalf of without ever having looked at them.
"""

from __future__ import annotations

import pytest

from apps.common.state_machine import IllegalTransitionError
from apps.provider.domain.verification import (
    VERIFY_MACHINE,
    ListingKind,
    ProviderType,
    VerifyState,
    is_sellable,
    may_own,
    path_to_verified,
)
from apps.provider.models import Provider, ProviderTypeChoice, VerifyStatus

S = VerifyState

#: §26.2's edges, transcribed. Equality below, not membership: the failure worth
#: catching is an edge somebody added.
EDGES: dict[VerifyState, frozenset[VerifyState]] = {
    S.DRAFT: frozenset({S.SUBMITTED}),
    S.SUBMITTED: frozenset({S.UNDER_REVIEW}),
    S.UNDER_REVIEW: frozenset({S.VERIFIED, S.REJECTED}),
    S.REJECTED: frozenset({S.SUBMITTED}),
    S.VERIFIED: frozenset({S.SUSPENDED}),
    S.SUSPENDED: frozenset({S.VERIFIED}),
}


class TestTheDeclaredSet:
    def test_it_starts_in_draft(self) -> None:
        """§7.5.3's default is 'DRAFT'."""
        assert VERIFY_MACHINE.initial is S.DRAFT

    def test_it_has_the_six_states_and_no_others(self) -> None:
        assert VERIFY_MACHINE.states == frozenset(VerifyState)

    @pytest.mark.parametrize("source", list(VerifyState))
    def test_every_state_goes_exactly_where_section_26_2_draws(self, source: VerifyState) -> None:
        assert VERIFY_MACHINE.allowed_targets(source) == EDGES[source]

    def test_no_state_is_terminal(self) -> None:
        """A rejected provider resubmits; a suspended one is reinstated."""
        assert not any(VERIFY_MACHINE.is_terminal(state) for state in VerifyState)


class TestThereAreNoShortcuts:
    @pytest.mark.parametrize("source", [S.DRAFT, S.SUBMITTED, S.REJECTED])
    def test_nothing_reaches_verified_without_review(self, source: VerifyState) -> None:
        with pytest.raises(IllegalTransitionError):
            VERIFY_MACHINE.transition(source, S.VERIFIED)

    def test_a_suspension_is_of_a_verified_provider(self) -> None:
        """Suspending someone who was never verified would leave a record that
        reads as a withdrawn verification that never happened."""
        with pytest.raises(IllegalTransitionError):
            VERIFY_MACHINE.transition(S.UNDER_REVIEW, S.SUSPENDED)

    def test_a_rejection_is_not_an_approval_later(self) -> None:
        with pytest.raises(IllegalTransitionError):
            VERIFY_MACHINE.transition(S.REJECTED, S.UNDER_REVIEW)


class TestSellability:
    """BR-037: "VERIFIED and unsuspended at the moment of confirmation"."""

    def test_a_verified_provider_can_be_sold(self) -> None:
        assert is_sellable(S.VERIFIED)

    @pytest.mark.parametrize("state", [s for s in VerifyState if s is not S.VERIFIED])
    def test_nobody_else_can(self, state: VerifyState) -> None:
        assert not is_sellable(state)


class TestTheWalkToVerified:
    @pytest.mark.parametrize("start", list(VerifyState))
    def test_every_hop_is_a_declared_edge_ending_verified(self, start: VerifyState) -> None:
        route = path_to_verified(start)
        current = start
        for step in route:
            current = VERIFY_MACHINE.transition(current, step)
        assert current is S.VERIFIED

    def test_a_draft_passes_through_submission_and_review(self) -> None:
        """The admin's "verify" action must leave the trail §26.2 describes."""
        assert path_to_verified(S.DRAFT) == (S.SUBMITTED, S.UNDER_REVIEW, S.VERIFIED)

    def test_a_verified_provider_needs_no_steps(self) -> None:
        assert path_to_verified(S.VERIFIED) == ()

    def test_a_broken_route_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the table changes under the routes, the walk refuses rather than
        handing back a path the machine would reject halfway along."""
        from apps.provider.domain import verification

        monkeypatch.setitem(verification._ROUTES, S.DRAFT, (S.VERIFIED,))
        with pytest.raises(AssertionError, match="not a declared edge"):
            path_to_verified(S.DRAFT)


class TestListingOwnership:
    """§7.5.3: "a provider may not own listings of a type inconsistent with
    provider_type"."""

    @pytest.mark.parametrize(
        ("provider_type", "kind"),
        [
            (ProviderType.ACTIVITY, ListingKind.ACTIVITY),
            (ProviderType.TRANSPORT, ListingKind.TRANSFER),
            (ProviderType.ACCOMMODATION, ListingKind.ACCOMMODATION),
        ],
    )
    def test_each_type_owns_its_own_kind(
        self, provider_type: ProviderType, kind: ListingKind
    ) -> None:
        assert may_own(provider_type, kind)

    @pytest.mark.parametrize(
        ("provider_type", "kind"),
        [
            (ProviderType.ACTIVITY, ListingKind.TRANSFER),
            (ProviderType.TRANSPORT, ListingKind.ACTIVITY),
            (ProviderType.ACCOMMODATION, ListingKind.ACTIVITY),
        ],
    )
    def test_and_no_other(self, provider_type: ProviderType, kind: ListingKind) -> None:
        assert not may_own(provider_type, kind)


class TestItAgreesWithTheColumns:
    def test_the_states_match_the_model_s_choices(self) -> None:
        assert {s.value for s in VerifyState} == set(VerifyStatus.values)

    def test_the_types_match_the_model_s_choices(self) -> None:
        assert {t.value for t in ProviderType} == set(ProviderTypeChoice.values)

    def test_the_default_status_is_the_machine_s_initial_state(self) -> None:
        field = Provider._meta.get_field("verify_status")
        assert field.default == VERIFY_MACHINE.initial.value
