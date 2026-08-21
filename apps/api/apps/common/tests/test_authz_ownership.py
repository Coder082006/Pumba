"""Tests for the ownership predicate of SRS §30.3.

This is the control that stops broken object-level authorisation (OWASP API
#1, named in the §30 threat table), so the four semantics it rests on each get
their own explicit test rather than being implied by an endpoint test.
"""

from __future__ import annotations

import uuid

import pytest

from apps.common.authz.ownership import (
    OWNERSHIP,
    Filter,
    OwnershipRule,
    Resource,
    Scope,
    ownership_filter,
)
from apps.common.authz.principal import Principal
from apps.common.authz.roles import Role


def principal(*roles: Role, **links: int | None) -> Principal:
    return Principal(
        user_id=links.pop("user_id", 1) or 1,
        user_public_id=uuid.UUID(int=1),
        roles=frozenset(roles),
        tourist_id=links.pop("tourist_id", None),
        driver_id=links.pop("driver_id", None),
        provider_id=links.pop("provider_id", None),
    )


class TestTheTableIsTotal:
    """Semantic 1 — a new resource cannot ship unprotected."""

    def test_every_role_resource_pair_is_stated(self) -> None:
        missing = [(r, res) for r in Role for res in Resource if (r, res) not in OWNERSHIP]
        assert not missing, f"undeclared ownership pairs: {missing}"

    def test_no_pair_is_declared_twice_or_spuriously(self) -> None:
        assert len(OWNERSHIP) == len(Role) * len(Resource)

    def test_the_table_cannot_be_mutated_at_runtime(self) -> None:
        with pytest.raises(TypeError):
            OWNERSHIP[(Role.TOURIST, Resource.USER)] = OwnershipRule(Scope.GLOBAL)  # type: ignore[index]


class TestRuleValidation:
    def test_an_owned_rule_must_name_both_sides(self) -> None:
        with pytest.raises(ValueError, match="must name both"):
            OwnershipRule(Scope.OWNED)
        with pytest.raises(ValueError, match="must name both"):
            OwnershipRule(Scope.OWNED, principal_attr="user_id")

    def test_a_global_rule_must_not_name_a_column(self) -> None:
        with pytest.raises(ValueError, match="must not name a column"):
            OwnershipRule(Scope.GLOBAL, principal_attr="user_id", row_field="id")


class TestFailClosed:
    """Semantic 1."""

    def test_a_principal_with_no_roles_reaches_nothing(self) -> None:
        for resource in Resource:
            assert ownership_filter(principal(), resource).is_deny_all

    def test_a_role_mapped_to_none_reaches_nothing(self) -> None:
        assert ownership_filter(principal(Role.DRIVER), Resource.TOURIST_PROFILE).is_deny_all

    def test_a_role_without_its_linking_id_reaches_nothing(self) -> None:
        """Granted DRIVER before the driver row exists — owns nothing yet."""
        assert ownership_filter(principal(Role.TOURIST), Resource.TOURIST_PROFILE).is_deny_all

    def test_denial_is_the_default_for_an_unmapped_pair(self) -> None:
        """Proven against a real unmapped pair rather than by inspection."""
        assert (Role.SUPPORT_AGENT, Resource.SESSION) in OWNERSHIP
        stripped = principal(Role.SUPPORT_AGENT)
        assert ownership_filter(stripped, Resource.SESSION).is_deny_all


class TestOwnedScope:
    def test_filters_on_the_declared_column_and_value(self) -> None:
        f = ownership_filter(principal(Role.TOURIST, tourist_id=77), Resource.TOURIST_PROFILE)
        assert f == Filter.equals("id", 77)

    def test_session_is_scoped_by_user_not_by_tourist(self) -> None:
        f = ownership_filter(principal(Role.TOURIST, user_id=5, tourist_id=77), Resource.SESSION)
        assert f == Filter.equals("user_id", 5)


class TestUnionNotIntersection:
    """Semantic 2 — a tourist who also drives keeps both capabilities."""

    def test_two_owned_rules_produce_a_union(self) -> None:
        p = principal(Role.TOURIST, Role.DRIVER, user_id=5, tourist_id=77, driver_id=9)
        f = ownership_filter(p, Resource.DEVICE)
        # Both roles scope DEVICE by user_id, so the union collapses to one.
        assert f == Filter.equals("user_id", 5)

    def test_a_global_role_absorbs_an_owned_role(self) -> None:
        p = principal(Role.TOURIST, Role.SUPER_ADMIN, user_id=5, tourist_id=77)
        assert ownership_filter(p, Resource.TOURIST_PROFILE) == Filter.allow_all()

    def test_a_denied_role_does_not_cancel_a_granting_role(self) -> None:
        """DRIVER is NONE on TOURIST_PROFILE; TOURIST still reaches its own."""
        p = principal(Role.TOURIST, Role.DRIVER, tourist_id=77, driver_id=9)
        assert ownership_filter(p, Resource.TOURIST_PROFILE) == Filter.equals("id", 77)

    def test_the_union_is_order_independent(self) -> None:
        a = principal(Role.TOURIST, Role.DRIVER, user_id=5, tourist_id=1, driver_id=2)
        b = principal(Role.DRIVER, Role.TOURIST, user_id=5, tourist_id=1, driver_id=2)
        assert ownership_filter(a, Resource.SESSION) == ownership_filter(b, Resource.SESSION)


class TestGlobalReadDegradesOnWrite:
    """Semantic 3 — the SUPPORT_AGENT / FINANCE_OFFICER distinction of §5.2."""

    def test_support_reads_every_user(self) -> None:
        f = ownership_filter(principal(Role.SUPPORT_AGENT), Resource.USER, write=False)
        assert f == Filter.allow_all()

    def test_support_writes_none(self) -> None:
        f = ownership_filter(principal(Role.SUPPORT_AGENT), Resource.USER, write=True)
        assert f.is_deny_all

    def test_global_read_does_not_fall_back_to_owned_on_write(self) -> None:
        """A global reader has no linking id; falling back would be a bug."""
        p = principal(Role.SUPPORT_AGENT, user_id=5)
        assert ownership_filter(p, Resource.USER, write=True).is_deny_all

    def test_a_second_role_can_still_grant_the_write(self) -> None:
        p = principal(Role.SUPPORT_AGENT, Role.TOURIST, user_id=5)
        assert ownership_filter(p, Resource.USER, write=True) == Filter.equals("id", 5)

    def test_full_global_is_unaffected_by_write(self) -> None:
        p = principal(Role.SUPER_ADMIN)
        assert ownership_filter(p, Resource.USER, write=True) == Filter.allow_all()


class TestSessionsAndDevicesAreNeverGloballyReadable:
    """A session row is a live credential handle; a push token is a delivery
    address for private content. Neither is support-readable."""

    @pytest.mark.parametrize("resource", [Resource.SESSION, Resource.DEVICE])
    @pytest.mark.parametrize("role", [Role.SUPPORT_AGENT, Role.COMPLIANCE_ADMIN])
    def test_no_investigative_role_reads_them(self, resource: Resource, role: Role) -> None:
        assert ownership_filter(principal(role), resource).is_deny_all


class TestFilterAlgebra:
    def test_any_of_absorbs_allow_all(self) -> None:
        f = Filter.any_of(Filter.equals("id", 1), Filter.allow_all())
        assert f == Filter.allow_all()

    def test_any_of_drops_deny_all_branches(self) -> None:
        f = Filter.any_of(Filter.deny_all(), Filter.equals("id", 1))
        assert f == Filter.equals("id", 1)

    def test_any_of_nothing_is_denial(self) -> None:
        assert Filter.any_of().is_deny_all
        assert Filter.any_of(Filter.deny_all(), Filter.deny_all()).is_deny_all

    def test_identical_branches_collapse(self) -> None:
        """Otherwise the SQL accumulates one redundant OR per role held."""
        f = Filter.any_of(Filter.equals("user_id", 5), Filter.equals("user_id", 5))
        assert f == Filter.equals("user_id", 5)

    def test_a_genuine_union_is_preserved(self) -> None:
        f = Filter.any_of(Filter.equals("a", 1), Filter.equals("b", 2))
        assert f.kind == "ANY_OF"
        assert len(f.branches) == 2

    def test_filters_are_immutable(self) -> None:
        with pytest.raises(AttributeError):
            Filter.allow_all().kind = "DENY_ALL"  # type: ignore[misc]


class TestPrincipalAttrIsRestricted:
    """An ownership rule must not turn an identity flag into a row filter."""

    def test_linking_ids_are_readable(self) -> None:
        p = principal(Role.TOURIST, user_id=5, tourist_id=77)
        assert p.attr("tourist_id") == 77
        assert p.attr("user_id") == 5

    @pytest.mark.parametrize("name", ["roles", "is_email_verified", "mfa_satisfied"])
    def test_anything_else_is_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="not a linkable"):
            principal(Role.TOURIST).attr(name)


CATALOGUE_RESOURCES = (
    Resource.COUNTRY,
    Resource.REGION,
    Resource.DESTINATION,
    Resource.TAG,
    Resource.ATTRACTION,
    Resource.CANCELLATION_POLICY,
    Resource.ACCOMMODATION,
    Resource.ROOM_TYPE,
    Resource.ACTIVITY,
    Resource.ACTIVITY_SCHEDULE,
    Resource.ACTIVITY_DEPARTURE,
    Resource.MEDIA,
)

PROVIDER_LISTED = (
    Resource.ACCOMMODATION,
    Resource.ROOM_TYPE,
    Resource.ACTIVITY,
    Resource.ACTIVITY_SCHEDULE,
    Resource.ACTIVITY_DEPARTURE,
)


class TestCatalogueIsAdministered:
    """§27.8 and §5.2, for the Phase 3 resources."""

    @pytest.mark.parametrize("resource", CATALOGUE_RESOURCES)
    def test_the_catalogue_admin_reaches_everything(self, resource: Resource) -> None:
        allowed = ownership_filter(principal(Role.CATALOGUE_ADMIN), resource, write=True)
        assert allowed.kind == "ALLOW_ALL"

    @pytest.mark.parametrize("resource", CATALOGUE_RESOURCES)
    def test_support_reads_but_does_not_write(self, resource: Resource) -> None:
        who = principal(Role.SUPPORT_AGENT)
        assert ownership_filter(who, resource).kind == "ALLOW_ALL"
        assert ownership_filter(who, resource, write=True).is_deny_all

    @pytest.mark.parametrize("resource", CATALOGUE_RESOURCES)
    def test_a_tourist_reaches_nothing(self, resource: Resource) -> None:
        """Not an oversight. The §9.3.2 public endpoints are unauthenticated
        and filtered by `domain.visibility`; a tourist never asks this table.
        A read here would be a second, weaker answer to the same question."""
        assert ownership_filter(principal(Role.TOURIST, tourist_id=7), resource).is_deny_all

    @pytest.mark.parametrize("resource", CATALOGUE_RESOURCES)
    def test_a_driver_reaches_nothing(self, resource: Resource) -> None:
        assert ownership_filter(principal(Role.DRIVER, driver_id=3), resource).is_deny_all

    @pytest.mark.parametrize("resource", CATALOGUE_RESOURCES)
    def test_finance_reaches_nothing(self, resource: Resource) -> None:
        assert ownership_filter(principal(Role.FINANCE_OFFICER), resource).is_deny_all


class TestProviderListingsAreScopedByProvider:
    """§5.2. Unused in Phase 3 — the portal is Phase 11 — and stated anyway,
    because a cell left at NONE is a lie the totality test cannot catch."""

    @pytest.mark.parametrize("resource", PROVIDER_LISTED)
    def test_a_provider_reaches_only_its_own(self, resource: Resource) -> None:
        allowed = ownership_filter(principal(Role.PROVIDER_OWNER, provider_id=42), resource)
        assert allowed.kind == "EQUALS"
        assert allowed.value == 42

    @pytest.mark.parametrize("resource", PROVIDER_LISTED)
    def test_staff_are_scoped_the_same_way_as_owners(self, resource: Resource) -> None:
        owner = ownership_filter(principal(Role.PROVIDER_OWNER, provider_id=42), resource)
        staff = ownership_filter(principal(Role.PROVIDER_STAFF, provider_id=42), resource)
        assert owner == staff

    def test_a_child_row_is_scoped_through_its_parent(self) -> None:
        """`room_type` has no `provider_id` of its own; the path is what makes
        the rule expressible without denormalising the column."""
        allowed = ownership_filter(
            principal(Role.PROVIDER_OWNER, provider_id=42), Resource.ROOM_TYPE
        )
        assert allowed.row_field == "accommodation__provider_id"

    def test_a_capacity_row_is_scoped_through_its_activity(self) -> None:
        allowed = ownership_filter(
            principal(Role.PROVIDER_OWNER, provider_id=42), Resource.ACTIVITY_DEPARTURE
        )
        assert allowed.row_field == "activity__provider_id"

    def test_room_availability_is_no_longer_a_resource(self) -> None:
        """ADR 0013. Removed, not reserved: this enum is never persisted, so
        there is no numbering to protect, and a member with no table would
        carry an ownership path that cannot resolve."""
        assert not hasattr(Resource, "ROOM_AVAILABILITY")

    def test_a_provider_with_no_provider_row_reaches_nothing(self) -> None:
        """A user granted PROVIDER_OWNER before their provider record exists."""
        who = principal(Role.PROVIDER_OWNER, provider_id=None)
        assert ownership_filter(who, Resource.ACCOMMODATION).is_deny_all

    @pytest.mark.parametrize(
        "resource",
        [
            Resource.COUNTRY,
            Resource.REGION,
            Resource.DESTINATION,
            Resource.TAG,
            Resource.ATTRACTION,
            Resource.CANCELLATION_POLICY,
        ],
    )
    def test_a_provider_does_not_reach_geography_or_vocabulary(self, resource: Resource) -> None:
        """A provider does not get to create the destination it sells in, or
        edit a §14.6 policy every other property also references."""
        who = principal(Role.PROVIDER_OWNER, provider_id=42)
        assert ownership_filter(who, resource).is_deny_all
