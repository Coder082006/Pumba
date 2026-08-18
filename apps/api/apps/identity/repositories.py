"""identity module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). All ORM writes; returns DTOs.

The services layer holds the policy; this holds the SQL. The split is what
lets `services.py` be read as a sequence of decisions rather than a sequence
of queries.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.common.authz import Principal
from apps.common.authz import Role as RoleEnum
from apps.identity.models import (
    Device,
    OneTimeToken,
    Role,
    Session,
    TokenPurpose,
    TouristProfile,
    User,
    UserRole,
    UserStatus,
)

__all__ = [
    "hash_token",
    "new_one_time_token",
    "find_user_by_email",
    "create_tourist",
    "grant_role",
    "principal_for",
    "record_login_success",
    "record_login_failure",
    "apply_lockout",
    "mark_email_verified",
    "set_password",
    "issue_one_time_token",
    "consume_one_time_token",
    "create_session",
    "load_session",
    "supersede_session",
    "revoke_family",
    "revoke_all_families_for_user",
    "register_device",
    "revoke_device",
]

#: A 256-bit URL-safe secret. `token_urlsafe(32)` is 43 characters — far
#: beyond guessing, and short enough to survive an email client's line
#: wrapping intact.
_TOKEN_BYTES = 32


def hash_token(raw: str) -> str:
    """What goes in the database. The plaintext lives only in the email."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_one_time_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_token(raw)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def find_user_by_email(email: str) -> User | None:
    return User.objects.filter(email=User.normalise_email(email)).first()


@transaction.atomic
def create_tourist(
    *,
    email: str,
    password_hash: str,
    first_name: str,
    last_name: str,
    nationality: str | None = None,
    locale: str = "en",
    preferred_currency: str = "USD",
    marketing_opt_in: bool = False,
) -> User:
    """§9.4.1: "creates user (status PENDING) and tourist_profile"."""
    user = User.objects.create(
        email=User.normalise_email(email),
        password=password_hash,
        status=UserStatus.PENDING,
    )
    TouristProfile.objects.create(
        user=user,
        first_name=first_name,
        last_name=last_name,
        nationality=nationality,
        locale=locale,
        preferred_currency=preferred_currency,
        marketing_opt_in=marketing_opt_in,
    )
    grant_role(user, RoleEnum.TOURIST)
    return user


def grant_role(user: User, role: RoleEnum, *, granted_by: User | None = None) -> UserRole:
    grant, _ = UserRole.objects.get_or_create(
        user=user,
        role=Role.objects.get(code=str(role)),
        defaults={"granted_by": granted_by},
    )
    return grant


def principal_for(user: User, *, mfa_satisfied: bool = False) -> Principal:
    """Build the authorisation value object for a user row.

    This is `get_principal()` from the §6.4 interface. `tourist_id` is read
    from the profile; `driver_id` and `provider_id` stay `None` until those
    modules exist in Phase 3 — a principal with no linked row simply owns
    nothing of that kind, which `ownership_filter` already handles.
    """
    profile = getattr(user, "tourist_profile", None)
    return Principal(
        user_id=user.pk,
        user_public_id=user.public_id,
        roles=frozenset(RoleEnum(ur.role.code) for ur in user.user_roles.all()),
        tourist_id=None if profile is None else profile.pk,
        is_email_verified=user.is_email_verified,
        mfa_satisfied=mfa_satisfied,
    )


def record_login_success(user: User, *, now: datetime) -> None:
    user.last_login = now
    user.failed_login_count = 0
    user.failed_login_window_started_at = None
    user.locked_until = None
    user.save(
        update_fields=[
            "last_login",
            "failed_login_count",
            "failed_login_window_started_at",
            "locked_until",
        ]
    )


def record_login_failure(user: User, *, count: int, window_started_at: datetime) -> None:
    user.failed_login_count = count
    user.failed_login_window_started_at = window_started_at
    user.save(update_fields=["failed_login_count", "failed_login_window_started_at"])


def apply_lockout(user: User, *, locked_until: datetime, window_started_at: datetime) -> None:
    user.locked_until = locked_until
    user.failed_login_count = 0
    user.failed_login_window_started_at = window_started_at
    user.lockout_count += 1
    user.save(
        update_fields=[
            "locked_until",
            "failed_login_count",
            "failed_login_window_started_at",
            "lockout_count",
        ]
    )


def mark_email_verified(user: User, *, now: datetime) -> None:
    """§9.4.1 leaves the user PENDING; verification is what activates it."""
    user.email_verified_at = now
    if user.status == UserStatus.PENDING:
        user.status = UserStatus.ACTIVE
    user.save(update_fields=["email_verified_at", "status"])


def set_password(user: User, password_hash: str) -> None:
    user.password = password_hash
    user.save(update_fields=["password"])


# ---------------------------------------------------------------------------
# One-time tokens
# ---------------------------------------------------------------------------


def issue_one_time_token(
    user: User, purpose: TokenPurpose, *, ttl: timedelta, now: datetime
) -> str:
    """Returns the plaintext, which the caller emails and then forgets.

    Any outstanding token for the same purpose is consumed first: a password
    reset requested twice must not leave the first link live, or an attacker
    who triggered one keeps a valid link after the user requests their own.
    """
    OneTimeToken.objects.filter(user=user, purpose=purpose, consumed_at__isnull=True).update(
        consumed_at=now
    )
    raw, hashed = new_one_time_token()
    OneTimeToken.objects.create(user=user, purpose=purpose, token_hash=hashed, expires_at=now + ttl)
    return raw


def consume_one_time_token(raw: str, purpose: TokenPurpose, *, now: datetime) -> User | None:
    """Atomically spend a token. `None` if unknown, expired or already used.

    The update is conditional and its row count is the check, so two
    simultaneous uses of one link cannot both succeed.
    """
    with transaction.atomic():
        spent = OneTimeToken.objects.filter(
            token_hash=hash_token(raw),
            purpose=purpose,
            consumed_at__isnull=True,
            expires_at__gt=now,
        ).update(consumed_at=now)
        if not spent:
            return None
        token = OneTimeToken.objects.select_related("user").get(token_hash=hash_token(raw))
        return token.user


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(
    user: User,
    *,
    jti: uuid.UUID,
    family_id: uuid.UUID,
    expires_at: datetime,
    ip: str | None = None,
    user_agent: str = "",
) -> Session:
    return Session.objects.create(
        user=user,
        jti=jti,
        family_id=family_id,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent[:400],
    )


def load_session(jti: uuid.UUID) -> Session | None:
    return Session.objects.select_related("user").filter(jti=jti).first()


def supersede_session(session: Session, *, successor: uuid.UUID) -> None:
    session.superseded_by = successor
    session.save(update_fields=["superseded_by"])


def revoke_family(family_id: uuid.UUID, *, now: datetime) -> int:
    """§30.2: "a replayed refresh token revokes the whole family"."""
    return Session.objects.filter(family_id=family_id, revoked_at__isnull=True).update(
        revoked_at=now
    )


def revoke_all_families_for_user(user: User, *, now: datetime) -> int:
    return Session.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=now)


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


@transaction.atomic
def register_device(
    user: User,
    *,
    platform: str,
    push_token: str,
    device_name: str = "",
    app_version: str = "",
    now: datetime | None = None,
) -> Device:
    """Idempotent by push token — §25.3 re-registers on every login.

    A live row for this token belonging to *anyone* is revoked first. The
    handset has changed hands or the account has changed, and leaving the old
    row live would deliver one user's itinerary to another user's phone.
    """
    moment = now or timezone.now()
    Device.objects.filter(push_token=push_token, revoked_at__isnull=True).exclude(user=user).update(
        revoked_at=moment
    )

    device, created = Device.objects.update_or_create(
        user=user,
        push_token=push_token,
        revoked_at=None,
        defaults={
            "platform": platform,
            "device_name": device_name,
            "app_version": app_version,
            "last_seen_at": moment,
        },
    )
    return device


def revoke_device(device: Device, *, now: datetime) -> None:
    device.revoked_at = now
    device.save(update_fields=["revoked_at"])
