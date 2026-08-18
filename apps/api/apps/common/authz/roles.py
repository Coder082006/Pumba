"""The role-permission model of SRS §5.2.

A role grants permission to an *operation class*; an ownership predicate then
restricts it to rows the principal owns (§30.3). Both checks must pass, and
this module is only the first of the two — see `ownership.py` for the second.

Permissions are operation classes rather than CRUD verbs on purpose. "May this
principal initiate a payment" is a question the role table can answer; "may
this principal UPDATE row 41" is not, and conflating them is how role checks
end up silently doing an ownership check badly.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "permissions_for",
    "mfa_mandatory",
    "MFA_MANDATORY_ROLES",
]


class Role(StrEnum):
    """SRS §5.2, in the order the table lists them."""

    TOURIST = "TOURIST"
    DRIVER = "DRIVER"
    PROVIDER_OWNER = "PROVIDER_OWNER"
    PROVIDER_STAFF = "PROVIDER_STAFF"
    SUPPORT_AGENT = "SUPPORT_AGENT"
    FINANCE_OFFICER = "FINANCE_OFFICER"
    CATALOGUE_ADMIN = "CATALOGUE_ADMIN"
    COMPLIANCE_ADMIN = "COMPLIANCE_ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class Permission(StrEnum):
    """Operation classes, derived from the "Key permissions" column of §5.2.

    Named for what the principal is permitted to *do*, not for the HTTP method
    or the table. A permission that cannot be stated without naming a row is a
    sign it belongs in `ownership.py` instead.
    """

    # Tourist journey
    PROFILE_WRITE = "PROFILE_WRITE"
    TRIP_READ = "TRIP_READ"
    TRIP_WRITE = "TRIP_WRITE"
    BOOKING_CREATE = "BOOKING_CREATE"
    BOOKING_CANCEL = "BOOKING_CANCEL"
    PAYMENT_INITIATE = "PAYMENT_INITIATE"
    MESSAGE_POST = "MESSAGE_POST"
    REVIEW_SUBMIT = "REVIEW_SUBMIT"
    DEVICE_REGISTER = "DEVICE_REGISTER"

    # Driver
    ASSIGNMENT_READ = "ASSIGNMENT_READ"
    AVAILABILITY_WRITE = "AVAILABILITY_WRITE"
    OFFER_RESPOND = "OFFER_RESPOND"
    ASSIGNMENT_PROGRESS = "ASSIGNMENT_PROGRESS"
    EARNINGS_READ = "EARNINGS_READ"

    # Provider
    PROVIDER_MANAGE = "PROVIDER_MANAGE"
    LISTING_MANAGE = "LISTING_MANAGE"
    AVAILABILITY_MANAGE = "AVAILABILITY_MANAGE"
    PROVIDER_BOOKING_MANAGE = "PROVIDER_BOOKING_MANAGE"
    STAFF_MANAGE = "STAFF_MANAGE"
    PAYOUT_ACCOUNT_MANAGE = "PAYOUT_ACCOUNT_MANAGE"

    # Support and administration
    USER_READ_ALL = "USER_READ_ALL"
    TICKET_MANAGE = "TICKET_MANAGE"
    GOODWILL_CREDIT_ISSUE = "GOODWILL_CREDIT_ISSUE"
    FINANCE_READ = "FINANCE_READ"
    REFUND_APPROVE = "REFUND_APPROVE"
    PAYOUT_APPROVE = "PAYOUT_APPROVE"
    REPORT_EXPORT = "REPORT_EXPORT"
    CATALOGUE_MANAGE = "CATALOGUE_MANAGE"
    VERIFICATION_DECIDE = "VERIFICATION_DECIDE"
    ACCOUNT_SUSPEND = "ACCOUNT_SUSPEND"
    REVIEW_MODERATE = "REVIEW_MODERATE"
    ROLE_MANAGE = "ROLE_MANAGE"
    SYSTEM_CONFIGURE = "SYSTEM_CONFIGURE"
    AUDIT_READ = "AUDIT_READ"


_TOURIST = frozenset(
    {
        Permission.PROFILE_WRITE,
        Permission.TRIP_READ,
        Permission.TRIP_WRITE,
        Permission.BOOKING_CREATE,
        Permission.BOOKING_CANCEL,
        Permission.PAYMENT_INITIATE,
        Permission.MESSAGE_POST,
        Permission.REVIEW_SUBMIT,
        Permission.DEVICE_REGISTER,
    }
)

_DRIVER = frozenset(
    {
        Permission.PROFILE_WRITE,
        Permission.ASSIGNMENT_READ,
        Permission.AVAILABILITY_WRITE,
        Permission.OFFER_RESPOND,
        Permission.ASSIGNMENT_PROGRESS,
        Permission.EARNINGS_READ,
        Permission.MESSAGE_POST,
        Permission.DEVICE_REGISTER,
    }
)

#: PROVIDER_STAFF is PROVIDER_OWNER minus payout and staff management — §5.2:
#: "no payout or banking access".
_PROVIDER_STAFF = frozenset(
    {
        Permission.PROFILE_WRITE,
        Permission.LISTING_MANAGE,
        Permission.AVAILABILITY_MANAGE,
        Permission.PROVIDER_BOOKING_MANAGE,
        Permission.MESSAGE_POST,
        Permission.DEVICE_REGISTER,
    }
)

_PROVIDER_OWNER = _PROVIDER_STAFF | {
    Permission.PROVIDER_MANAGE,
    Permission.STAFF_MANAGE,
    Permission.PAYOUT_ACCOUNT_MANAGE,
    Permission.EARNINGS_READ,
}

#: §5.2: "Read all users, trips, bookings; create/annotate tickets; issue
#: goodwill credits up to configured cap; cannot alter payments or catalogue."
_SUPPORT_AGENT = frozenset(
    {
        Permission.USER_READ_ALL,
        Permission.TRIP_READ,
        Permission.TICKET_MANAGE,
        Permission.GOODWILL_CREDIT_ISSUE,
    }
)

_FINANCE_OFFICER = frozenset(
    {
        Permission.FINANCE_READ,
        Permission.REFUND_APPROVE,
        Permission.PAYOUT_APPROVE,
        Permission.REPORT_EXPORT,
    }
)

_CATALOGUE_ADMIN = frozenset({Permission.CATALOGUE_MANAGE})

_COMPLIANCE_ADMIN = frozenset(
    {
        Permission.VERIFICATION_DECIDE,
        Permission.ACCOUNT_SUSPEND,
        Permission.REVIEW_MODERATE,
        Permission.USER_READ_ALL,
    }
)

#: §5.2: "All of the above; manage roles and system configuration; read audit
#: log." Composed rather than enumerated, so a permission added to any role is
#: automatically held by SUPER_ADMIN and cannot be forgotten here.
_SUPER_ADMIN = (
    _TOURIST
    | _DRIVER
    | _PROVIDER_OWNER
    | _SUPPORT_AGENT
    | _FINANCE_OFFICER
    | _CATALOGUE_ADMIN
    | _COMPLIANCE_ADMIN
    | {Permission.ROLE_MANAGE, Permission.SYSTEM_CONFIGURE, Permission.AUDIT_READ}
)


ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.TOURIST: _TOURIST,
        Role.DRIVER: _DRIVER,
        Role.PROVIDER_OWNER: frozenset(_PROVIDER_OWNER),
        Role.PROVIDER_STAFF: _PROVIDER_STAFF,
        Role.SUPPORT_AGENT: _SUPPORT_AGENT,
        Role.FINANCE_OFFICER: _FINANCE_OFFICER,
        Role.CATALOGUE_ADMIN: _CATALOGUE_ADMIN,
        Role.COMPLIANCE_ADMIN: _COMPLIANCE_ADMIN,
        Role.SUPER_ADMIN: frozenset(_SUPER_ADMIN),
    }
)


#: SRS §30.2: "TOTP MFA is mandatory for PROVIDER_* and all administrative
#: roles and optional for tourists and drivers."
MFA_MANDATORY_ROLES: frozenset[Role] = frozenset(
    {
        Role.PROVIDER_OWNER,
        Role.PROVIDER_STAFF,
        Role.SUPPORT_AGENT,
        Role.FINANCE_OFFICER,
        Role.CATALOGUE_ADMIN,
        Role.COMPLIANCE_ADMIN,
        Role.SUPER_ADMIN,
    }
)


def permissions_for(roles: frozenset[Role]) -> frozenset[Permission]:
    """The union of every permission granted by any role the principal holds."""
    granted: frozenset[Permission] = frozenset()
    for role in roles:
        granted |= ROLE_PERMISSIONS[role]
    return granted


def mfa_mandatory(roles: frozenset[Role]) -> bool:
    """Whether TOTP is required before this principal may act (§30.2).

    Keyed off the role set rather than a URL prefix, so an unguarded provider
    endpoint cannot become a way around the requirement. Mandatory for *any*
    qualifying role: a user who is both a tourist and a provider owner must
    still enrol, because the account can reach the console.
    """
    return bool(roles & MFA_MANDATORY_ROLES)
