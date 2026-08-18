"""identity module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). Read queries; returns DTOs.

**Every query that can return another principal's row goes through
`apps.common.scoping.scoped`.** SRS §30.3: the ownership check is a queryset
filter applied here, never a comparison after the fetch. A selector that
takes an id and no principal is the shape of the bug this layer exists to
prevent, so the ones that exist take both.
"""

from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from apps.common.authz import Principal, Resource
from apps.common.scoping import scoped
from apps.identity.dto import DeviceDTO, TouristProfileDTO, UserDTO
from apps.identity.models import Device, User

__all__ = [
    "to_user_dto",
    "to_device_dto",
    "get_user_for_principal",
    "list_devices_for_principal",
    "get_device_for_principal",
]


def to_user_dto(user: User) -> UserDTO:
    profile = getattr(user, "tourist_profile", None)
    return UserDTO(
        public_id=user.public_id,
        email=user.email,
        status=user.status,
        email_verified=user.is_email_verified,
        mfa_enrolled=user.has_mfa,
        roles=frozenset(ur.role.code for ur in user.user_roles.all()),
        created_at=user.created_at,
        profile=(
            None
            if profile is None
            else TouristProfileDTO(
                public_id=profile.public_id,
                first_name=profile.first_name,
                last_name=profile.last_name,
                nationality=profile.nationality,
                locale=profile.locale,
                preferred_currency=profile.preferred_currency,
                marketing_opt_in=profile.marketing_opt_in,
            )
        ),
    )


def to_device_dto(device: Device) -> DeviceDTO:
    return DeviceDTO(
        public_id=device.public_id,
        platform=device.platform,
        device_name=device.device_name,
        last_seen_at=device.last_seen_at,
    )


def users_visible_to(principal: Principal, *, write: bool = False) -> QuerySet[User]:
    return scoped(
        User.objects.select_related("tourist_profile").prefetch_related("user_roles__role"),
        principal,
        Resource.USER,
        write=write,
    )


def get_user_for_principal(principal: Principal, public_id: UUID) -> UserDTO | None:
    """`None` rather than raising, so the caller renders 404 either way.

    A principal who does not own this row and a `public_id` that does not
    exist are the same answer on purpose — §30.3: "403 confirms existence".
    """
    user = users_visible_to(principal).filter(public_id=public_id).first()
    return None if user is None else to_user_dto(user)


def devices_visible_to(principal: Principal, *, write: bool = False) -> QuerySet[Device]:
    return scoped(
        Device.objects.filter(revoked_at__isnull=True), principal, Resource.DEVICE, write=write
    )


def list_devices_for_principal(principal: Principal) -> list[DeviceDTO]:
    return [to_device_dto(d) for d in devices_visible_to(principal).order_by("-last_seen_at")]


def get_device_for_principal(principal: Principal, public_id: UUID) -> DeviceDTO | None:
    device = devices_visible_to(principal).filter(public_id=public_id).first()
    return None if device is None else to_device_dto(device)
