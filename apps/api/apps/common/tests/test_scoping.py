"""Tests for the Filter -> queryset translation of SRS §30.3.

No database is touched: querysets are lazy, so the restriction can be
asserted structurally without ever executing SQL. That is the point of
keeping the decision pure and the translation this thin.
"""

from __future__ import annotations

import uuid

import pytest

from apps.common.authz import Filter, Principal, Resource, Role
from apps.common.idempotency import IdempotencyRecord
from apps.common.scoping import UnscopableFilterError, apply_filter, as_q, scoped


def qs():  # type: ignore[no-untyped-def]
    """A stand-in queryset.

    `IdempotencyRecord` is used because it is the only model `common` owns.
    The translation under test is model-agnostic — it maps a column name and a
    value onto a `Q` — so any real model exercises it, and using a real one
    means a filter on a non-existent column fails here rather than in
    production.
    """
    return IdempotencyRecord.objects.all()


def principal(*roles: Role, user_id: int = 5) -> Principal:
    return Principal(user_id=user_id, user_public_id=uuid.UUID(int=1), roles=frozenset(roles))


class TestAsQ:
    def test_equals_becomes_a_single_lookup(self) -> None:
        q = as_q(Filter.equals("principal_id", 7))
        assert q.children == [("principal_id", 7)]

    def test_any_of_becomes_a_disjunction(self) -> None:
        q = as_q(Filter.any_of(Filter.equals("principal_id", 1), Filter.equals("key", "a")))
        assert q.connector == "OR"
        assert len(q.children) == 2

    @pytest.mark.parametrize("f", [Filter.allow_all(), Filter.deny_all()])
    def test_the_identities_have_no_q_representation(self, f: Filter) -> None:
        """Q() matches everything and ~Q() is not reliably empty.

        Silently returning either would turn "cannot express this
        restriction" into "applied no restriction".
        """
        with pytest.raises(UnscopableFilterError):
            as_q(f)


class TestApplyFilter:
    def test_allow_all_leaves_the_queryset_untouched(self) -> None:
        base = qs()
        assert apply_filter(base, Filter.allow_all()) is base

    def test_deny_all_produces_an_empty_queryset(self) -> None:
        """Which is what makes get_object() raise Http404 rather than 403."""
        assert apply_filter(qs(), Filter.deny_all()).query.is_empty()

    def test_equals_narrows_the_queryset(self) -> None:
        restricted = apply_filter(qs(), Filter.equals("principal_id", 7))
        assert not restricted.query.is_empty()
        assert "principal_id" in str(restricted.query)

    def test_a_union_narrows_to_either_branch(self) -> None:
        f = Filter.any_of(Filter.equals("principal_id", 1), Filter.equals("principal_id", 2))
        sql = str(apply_filter(qs(), f).query)
        assert " OR " in sql


class TestScoped:
    def test_an_absent_principal_reaches_nothing(self) -> None:
        """An anonymous request reaching here is a bug in the permission
        classes, and denial is the safe response to a bug in a check."""
        assert scoped(qs(), None, Resource.DEVICE).query.is_empty()

    def test_a_principal_with_no_roles_reaches_nothing(self) -> None:
        assert scoped(qs(), principal(), Resource.DEVICE).query.is_empty()

    def test_an_owner_is_restricted_to_their_own_rows(self) -> None:
        # Resource.USER, whose rule scopes on `id` — a column the stand-in
        # model actually has, so the restriction is asserted against a real
        # query rather than a mock.
        restricted = scoped(qs(), principal(Role.TOURIST, user_id=5), Resource.USER)
        assert not restricted.query.is_empty()
        assert '"id" = 5' in str(restricted.query)

    def test_a_global_role_is_unrestricted(self) -> None:
        base = qs()
        assert scoped(base, principal(Role.SUPER_ADMIN), Resource.DEVICE) is base

    def test_write_is_denied_where_only_global_read_is_granted(self) -> None:
        support = principal(Role.SUPPORT_AGENT)
        assert not scoped(qs(), support, Resource.USER, write=False).query.is_empty()
        assert scoped(qs(), support, Resource.USER, write=True).query.is_empty()

    def test_reads_default_to_read_semantics(self) -> None:
        """`write` defaults to False, so forgetting it cannot widen access."""
        support = principal(Role.SUPPORT_AGENT)
        assert scoped(qs(), support, Resource.USER).query.is_empty() is False
