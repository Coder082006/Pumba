"""Check 1 of SRS §30.3 — the role check, declarative per view.

    ROLE CHECK      does this principal's role permit this operation class?
                    (DRF permission class, declarative per view)

Check 2, the ownership predicate, is deliberately **not** here. DRF's
`has_object_permission` runs after `get_object()` has already fetched the row,
which is the "fetch then compare" §30.3 forbids because it leaks existence.
Ownership is a queryset filter — see `apps.common.mixins.ScopedQuerysetMixin`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from rest_framework.permissions import BasePermission

from apps.common.authentication import principal_from_request
from apps.common.authz import Permission, Role, mfa_mandatory

if TYPE_CHECKING:
    # Imported for annotations only. DRF resolves DEFAULT_PERMISSION_CLASSES
    # while `rest_framework.views` is still initialising, so importing
    # APIView here at runtime is a circular import.
    from rest_framework.request import Request
    from rest_framework.views import APIView

__all__ = [
    "IsAuthenticatedPrincipal",
    "HasPermission",
    "EmailVerified",
    "MfaSatisfied",
    "HasRole",
    "IsTourist",
    "tourist_id_of",
    "CATALOGUE_ADMIN_PERMISSIONS",
]


class IsAuthenticatedPrincipal(BasePermission):
    """A principal was built for this request."""

    message = "Authentication is required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return principal_from_request(request) is not None


class HasPermission(BasePermission):
    """Does the principal's role set grant this operation class?

    Used as `HasPermission.for_(Permission.TRIP_WRITE)`, which builds a
    subclass rather than an instance because DRF instantiates the classes in
    `permission_classes` itself.
    """

    required: ClassVar[Permission | None] = None
    message = "Your role does not permit this operation."

    @classmethod
    def for_(cls, permission: Permission) -> type[HasPermission]:
        return type(
            f"HasPermission{permission.value.title().replace('_', '')}",
            (cls,),
            {"required": permission},
        )

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        if principal is None or self.required is None:
            # A missing `required` is a misconfigured view, not a grant.
            return False
        return principal.has(self.required)


class HasRole(BasePermission):
    """Occasionally a view is scoped to a role rather than an operation.

    Prefer `HasPermission`: a role check spreads the §5.2 table across views,
    where a permission check keeps it in one place.
    """

    required_role: ClassVar[Role | None] = None
    message = "Your role does not permit this operation."

    @classmethod
    def for_(cls, role: Role) -> type[HasRole]:
        return type(f"HasRole{role.value.title()}", (cls,), {"required_role": role})

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        if principal is None or self.required_role is None:
            return False
        return principal.has_role(self.required_role)


class EmailVerified(BasePermission):
    """§30.2: "Email verification required before booking"."""

    message = "Verify your email address before continuing."
    code = "EMAIL_NOT_VERIFIED"

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        return principal is not None and principal.is_email_verified


class MfaSatisfied(BasePermission):
    """§30.2: TOTP is mandatory for PROVIDER_* and administrative roles.

    Keyed off the principal's *roles*, not off the URL, so an unguarded
    provider endpoint cannot become a way around the requirement. A tourist
    passes without enrolling; a provider owner does not.
    """

    message = "Two-factor authentication is required for this account."
    code = "MFA_REQUIRED"

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        if principal is None:
            return False
        if not mfa_mandatory(principal.roles):
            return True
        return principal.mfa_satisfied


def deny_object_permission(*args: Any, **kwargs: Any) -> bool:
    """A tripwire for `has_object_permission`.

    Installed on the project's base view. Reintroducing fetch-then-compare is
    then a loud failure in development rather than a quiet regression in
    production — §30.3 is explicit that comparing after the fetch leaks
    existence, and a convention that relies on memory is not a control.
    """
    raise AssertionError(
        "has_object_permission is not used in this project. Ownership is enforced "
        "by filtering the queryset (apps.common.mixins.ScopedQuerysetMixin); "
        "comparing after the fetch leaks existence — SRS §30.3."
    )


class IsTourist(BasePermission):
    """The principal has a tourist profile.

    Not "the principal owns this row" — see the module docstring. This is a
    question about the caller alone, which is why answering it with 403
    discloses nothing about any particular trip.

    **It lives in `common` because two modules ask it.** It was `trip`'s until
    Phase 5: §9.4.5's quote is `booking`'s use case (ADR 0022) and needs the
    same gate, and `apps.trip.permissions` is private to `trip` under §6.5
    rule 1. A copy in `booking` would be a second answer to "who is a
    tourist", and the one that drifts is always the copy.
    """

    message = "A tourist profile is required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        principal = principal_from_request(request)
        return principal is not None and principal.tourist_id is not None


def tourist_id_of(request: Request) -> int:
    """The caller's `tourist_profile.id`, for a service's `tourist_id`.

    `IsTourist` has already run, so both the principal and the id are present;
    the assertions state that rather than defending against it, because a view
    reachable without them would be a routing bug and silently defaulting to
    some other tourist is the worst possible recovery.
    """
    principal = principal_from_request(request)
    assert principal is not None, "IsTourist should have refused an anonymous request"
    assert principal.tourist_id is not None, "IsTourist should have refused a non-tourist"
    return principal.tourist_id


#: §5.2 grants `CATALOGUE_MANAGE` to CATALOGUE_ADMIN and, by composition, to
#: SUPER_ADMIN — and to nobody else: *"cannot alter payments or catalogue"* is
#: the sentence that keeps SUPPORT_AGENT off this surface. §30.2 adds the two
#: obligations that come with holding an administrative role at all: a verified
#: address, and TOTP satisfied *on this session* rather than merely enrolled.
#:
#: Here rather than in `catalogue.views` because two modules mount surfaces
#: behind it: §27.8's console and §26.5's departure calendar, which `inventory`
#: owns (ADR 0011) and which may not import `apps.catalogue.views` (contract
#: `private-catalogue`). A second list would be a second answer to "who may
#: edit the catalogue", and the one that drifted would drift silently.
CATALOGUE_ADMIN_PERMISSIONS: list[type[BasePermission]] = [
    IsAuthenticatedPrincipal,
    HasPermission.for_(Permission.CATALOGUE_MANAGE),
    EmailVerified,
    MfaSatisfied,
]
