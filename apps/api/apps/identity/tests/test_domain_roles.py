"""Tests for the §5.2 role-permission model."""

from __future__ import annotations

import pytest

from apps.identity.domain.roles import (
    MFA_MANDATORY_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    mfa_mandatory,
    permissions_for,
)


class TestRoleTableIsComplete:
    def test_every_role_has_a_permission_set(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(Role)

    def test_no_role_is_granted_nothing(self) -> None:
        """An empty grant means a role was declared and then forgotten."""
        for role, granted in ROLE_PERMISSIONS.items():
            assert granted, f"{role} grants no permission"

    def test_every_permission_is_granted_to_someone(self) -> None:
        """An ungranted permission is dead code, or a role table omission."""
        granted: set[Permission] = set()
        for permissions in ROLE_PERMISSIONS.values():
            granted |= permissions
        assert granted == set(Permission)

    def test_the_table_cannot_be_mutated_at_runtime(self) -> None:
        with pytest.raises(TypeError):
            ROLE_PERMISSIONS[Role.TOURIST] = frozenset()  # type: ignore[index]


class TestProviderStaffIsRestricted:
    """§5.2: PROVIDER_STAFF has "no payout or banking access"."""

    @pytest.mark.parametrize(
        "withheld",
        [Permission.PAYOUT_ACCOUNT_MANAGE, Permission.STAFF_MANAGE, Permission.PROVIDER_MANAGE],
    )
    def test_staff_lack_what_only_the_owner_may_do(self, withheld: Permission) -> None:
        assert withheld not in ROLE_PERMISSIONS[Role.PROVIDER_STAFF]
        assert withheld in ROLE_PERMISSIONS[Role.PROVIDER_OWNER]

    def test_staff_can_still_run_the_day_to_day(self) -> None:
        staff = ROLE_PERMISSIONS[Role.PROVIDER_STAFF]
        assert Permission.LISTING_MANAGE in staff
        assert Permission.AVAILABILITY_MANAGE in staff
        assert Permission.PROVIDER_BOOKING_MANAGE in staff


class TestSupportAgentIsRestricted:
    """§5.2: "cannot alter payments or catalogue"."""

    @pytest.mark.parametrize(
        "withheld",
        [
            Permission.REFUND_APPROVE,
            Permission.PAYOUT_APPROVE,
            Permission.CATALOGUE_MANAGE,
            Permission.PAYMENT_INITIATE,
        ],
    )
    def test_support_cannot_touch_money_or_catalogue(self, withheld: Permission) -> None:
        assert withheld not in ROLE_PERMISSIONS[Role.SUPPORT_AGENT]


class TestSuperAdmin:
    def test_holds_every_permission_any_other_role_holds(self) -> None:
        """§5.2: "All of the above"."""
        others: set[Permission] = set()
        for role, permissions in ROLE_PERMISSIONS.items():
            if role is not Role.SUPER_ADMIN:
                others |= permissions
        assert others <= ROLE_PERMISSIONS[Role.SUPER_ADMIN]

    def test_holds_the_three_powers_reserved_to_it(self) -> None:
        reserved = {Permission.ROLE_MANAGE, Permission.SYSTEM_CONFIGURE, Permission.AUDIT_READ}
        assert reserved <= ROLE_PERMISSIONS[Role.SUPER_ADMIN]
        for role, permissions in ROLE_PERMISSIONS.items():
            if role is not Role.SUPER_ADMIN:
                assert not (reserved & permissions), f"{role} holds a reserved permission"


class TestPermissionsFor:
    def test_no_roles_grants_nothing(self) -> None:
        assert permissions_for(frozenset()) == frozenset()

    def test_a_single_role_grants_its_own_set(self) -> None:
        assert permissions_for(frozenset({Role.TOURIST})) == ROLE_PERMISSIONS[Role.TOURIST]

    def test_multiple_roles_union_rather_than_intersect(self) -> None:
        """A tourist who also drives keeps both capabilities."""
        combined = permissions_for(frozenset({Role.TOURIST, Role.DRIVER}))
        assert Permission.TRIP_WRITE in combined
        assert Permission.OFFER_RESPOND in combined
        assert combined == ROLE_PERMISSIONS[Role.TOURIST] | ROLE_PERMISSIONS[Role.DRIVER]


class TestMfaMandatory:
    """§30.2: mandatory for PROVIDER_* and administrative roles."""

    @pytest.mark.parametrize("role", sorted(MFA_MANDATORY_ROLES))
    def test_mandatory_roles_require_totp(self, role: Role) -> None:
        assert mfa_mandatory(frozenset({role})) is True

    @pytest.mark.parametrize("role", [Role.TOURIST, Role.DRIVER])
    def test_tourists_and_drivers_are_optional(self, role: Role) -> None:
        assert mfa_mandatory(frozenset({role})) is False

    def test_no_roles_does_not_require_totp(self) -> None:
        assert mfa_mandatory(frozenset()) is False

    def test_one_qualifying_role_is_enough(self) -> None:
        """The account can reach the console, so the account must enrol."""
        assert mfa_mandatory(frozenset({Role.TOURIST, Role.PROVIDER_OWNER})) is True

    def test_every_provider_role_qualifies(self) -> None:
        assert {Role.PROVIDER_OWNER, Role.PROVIDER_STAFF} <= MFA_MANDATORY_ROLES

    def test_the_mandatory_set_is_exactly_the_non_tourist_non_driver_roles(self) -> None:
        assert set(Role) - {Role.TOURIST, Role.DRIVER} == MFA_MANDATORY_ROLES
