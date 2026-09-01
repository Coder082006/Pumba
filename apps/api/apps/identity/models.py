"""identity module — SRS §6.4.

Owns:
        user, role, user_role, tourist_profile, session, device

Interface:  authenticate(), issue_tokens(), get_principal()
Depends on: —
Layer:      L0

Data-access layer (SRS §8.2 layer 4).

`user` and `tourist_profile` follow §7.5.1 and §7.5.2 column for column.
`role` and `user_role` exist only as boxes in the §7.3 ERD, and `session` and
`device` are named in §6.4 but specified nowhere in the document — those four
are designed here against the §7.2 conventions and recorded in ADR 0007.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser
from django.db import models
from django.utils import timezone

from apps.common.fields import CITextField
from apps.common.models import BaseModel, SoftDeleteManager, SoftDeleteModel, TimestampedModel

__all__ = [
    "UserStatus",
    "User",
    "Role",
    "UserRole",
    "TouristProfile",
    "Session",
    "DevicePlatform",
    "Device",
    "TokenPurpose",
    "OneTimeToken",
]


class UserStatus(models.TextChoices):
    """§7.5.1: PENDING/ACTIVE/SUSPENDED/CLOSED."""

    PENDING = "PENDING", "Pending verification"
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    CLOSED = "CLOSED", "Closed"


class UserManager(SoftDeleteManager["User"]):
    """Excludes soft-deleted rows, and resolves a principal by email.

    `get_by_natural_key` is what Django's authentication machinery calls. It
    goes through this manager, so a closed-and-erased account cannot be
    resolved by an authentication backend — the row is invisible by default,
    and `all_objects` is the deliberate opt-in for audit.
    """

    def get_by_natural_key(self, username: str | None) -> User:
        return self.get(email=username)


class User(AbstractBaseUser, SoftDeleteModel):
    """SRS §7.5.1.

    `PermissionsMixin` is deliberately not inherited. Django's group and
    permission tables are a second, parallel authorisation system, and §5.2
    and §30.3 specify our own — two systems means two answers to "may this
    principal do this", and the wrong one eventually wins.
    """

    # Deliberately NOT `unique=True`: that emits an unconditional unique
    # index, which sits alongside `user_email_unique_alive` and silently
    # defeats it — §7.7 requires soft-deleted rows to be excluded from
    # uniqueness so that "re-registration after account closure remains
    # possible". The partial constraint in Meta is the only uniqueness here.
    email = CITextField()
    phone_e164 = models.CharField(max_length=20, null=True, blank=True, default=None)

    # AbstractBaseUser calls this `password`; §7.5.1 calls the column
    # `password_hash`. The column name is what matters for the schema.
    password = models.CharField(max_length=255, db_column="password_hash")

    status = models.CharField(
        max_length=20, choices=UserStatus.choices, default=UserStatus.PENDING, db_index=True
    )
    email_verified_at = models.DateTimeField(null=True, blank=True, default=None)
    phone_verified_at = models.DateTimeField(null=True, blank=True, default=None)

    #: §7.5.1: "Encrypted TOTP seed; required for staff/provider roles".
    #: Holds a `ports.crypto.Ciphertext` blob, never a bare seed.
    mfa_secret = models.BinaryField(null=True, blank=True, default=None, editable=False)
    mfa_enrolled_at = models.DateTimeField(null=True, blank=True, default=None)

    failed_login_count = models.SmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True, default=None)

    # Two columns §7.5.1 omits but §30.2's wording requires. "10 failed
    # attempts in 15 minutes" needs to know when the current run began, and
    # "exponentially increasing lockout" needs to know how many times this
    # account has already been locked. Without them the policy degrades to a
    # plain counter with a fixed penalty, which is not what §30.2 specifies.
    failed_login_window_started_at = models.DateTimeField(null=True, blank=True, default=None)
    lockout_count = models.IntegerField(default=0)

    last_login = models.DateTimeField(null=True, blank=True, db_column="last_login_at")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []
    EMAIL_FIELD = "email"

    objects: ClassVar[UserManager] = UserManager()
    all_objects: ClassVar[models.Manager[User]] = models.Manager()

    class Meta:
        db_table = "user"
        constraints = [
            # §7.5.1: "UNIQUE(email) WHERE deleted_at IS NULL" — partial, so
            # §7.7's "re-registration after account closure remains possible".
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(deleted_at__isnull=True),
                name="user_email_unique_alive",
            ),
            models.UniqueConstraint(
                fields=["phone_e164"],
                condition=models.Q(phone_e164__isnull=False, deleted_at__isnull=True),
                name="user_phone_unique_alive",
            ),
        ]
        indexes = [models.Index(fields=["status"], name="user_status_idx")]

    def __str__(self) -> str:
        return f"User({self.public_id})"

    @property
    def is_active(self) -> bool:  # type: ignore[override]
        """Django asks this on every authentication.

        Derived from `status` rather than stored separately: two sources of
        truth for "may this account log in" is one more than is safe.
        """
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def has_mfa(self) -> bool:
        return self.mfa_secret is not None and self.mfa_enrolled_at is not None

    @staticmethod
    def normalise_email(value: str) -> str:
        """§7.5.1 makes the column case-insensitive; this normalises what is
        stored, so one address does not appear in two spellings across
        exports, audit rows and notification addresses."""
        return value.strip().casefold()

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.email:
            self.email = self.normalise_email(self.email)
        super().save(*args, **kwargs)


class Role(TimestampedModel):
    """§7.3 ERD: id, code, name.

    A table rather than only an enum because §5.2 grants are administrator-
    managed and audited (`ROLE_MANAGE`). `code` is the shared vocabulary of
    `apps.common.authz.Role`, and a data migration seeds the nine rows.
    """

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        db_table = "role"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class UserRole(TimestampedModel):
    """§7.3 ERD: the M:N grant, with `granted_at`."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_roles")
    granted_at = models.DateTimeField(default=timezone.now)
    # Nullable: the seed loader and the registration path grant TOURIST with
    # no human actor. SET_NULL rather than CASCADE — removing an administrator
    # must not delete the record of what they granted.
    granted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="roles_granted"
    )

    class Meta:
        db_table = "user_role"
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="user_role_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.role_id}"


class TouristProfile(BaseModel):
    """SRS §7.5.2."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="tourist_profile")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    nationality = models.CharField(max_length=2, null=True, blank=True, default=None)
    date_of_birth = models.DateField(null=True, blank=True, default=None)

    #: §7.5.2: "Encrypted at rest; collected only when a booked service
    #: requires it (BR-072)". A `ports.crypto.Ciphertext` blob.
    passport_reference = models.BinaryField(null=True, blank=True, default=None, editable=False)

    emergency_contact = models.JSONField(null=True, blank=True, default=None)
    locale = models.CharField(max_length=10, default="en")
    preferred_currency = models.CharField(max_length=3, default="USD")
    interest_tags = models.JSONField(default=list, blank=True)
    marketing_opt_in = models.BooleanField(default=False)

    class Meta:
        db_table = "tourist_profile"

    def __str__(self) -> str:
        return f"TouristProfile({self.public_id})"


class Session(TimestampedModel):
    """A refresh token in a rotation family — SRS §30.2. Designed; ADR 0007.

    One row per issued refresh token, not one per login. Rotation appends a
    row and stamps `superseded_by` on its predecessor, so the family is an
    append-only chain and the reuse detection of `domain/tokens.py` is a
    lookup rather than an inference.

    Not a `BaseModel`: `jti` is already the external identifier, it is inside
    a signed token rather than in a URL, and a second UUID would only invite
    confusion about which one addresses the row.
    """

    jti = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    family_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")

    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True, default=None)
    superseded_by = models.UUIDField(null=True, blank=True, default=None)

    # §41.13 investigation context, and what makes "alert the user" specific
    # enough to act on: an unrecognised city is the signal, not the fact.
    ip = models.GenericIPAddressField(null=True, blank=True, default=None)
    user_agent = models.CharField(max_length=400, blank=True, default="")

    class Meta:
        db_table = "session"
        indexes = [
            models.Index(fields=["user", "-issued_at"], name="session_user_time_idx"),
            # The expiry sweeper scans only live rows.
            models.Index(
                fields=["expires_at"],
                condition=models.Q(revoked_at__isnull=True),
                name="session_live_expiry_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Session({self.jti})"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.superseded_by is None


class DevicePlatform(models.TextChoices):
    IOS = "IOS", "iOS"
    ANDROID = "ANDROID", "Android"
    WEB = "WEB", "Web"


class Device(BaseModel):
    """A push destination — SRS §6.4, §25.3. Designed; ADR 0007.

    §25.3: "The device registers its token at login and on token rotation."
    A push token is a delivery address for private itinerary content, which
    is why `ownership.py` lets nobody but the owner read one.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    platform = models.CharField(max_length=10, choices=DevicePlatform.choices)
    push_token = models.CharField(max_length=512)
    device_name = models.CharField(max_length=120, blank=True, default="")
    app_version = models.CharField(max_length=32, blank=True, default="")
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "device"
        constraints = [
            # A push token identifies a physical handset. Two live rows for
            # one token would send another user's itinerary to it after a
            # device changes hands.
            models.UniqueConstraint(
                fields=["push_token"],
                condition=models.Q(revoked_at__isnull=True),
                name="device_push_token_unique_live",
            ),
        ]
        indexes = [models.Index(fields=["user", "-last_seen_at"], name="device_user_seen_idx")]

    def __str__(self) -> str:
        return f"Device({self.public_id})"


class TokenPurpose(models.TextChoices):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION", "Email verification"
    #: The six digits in the same email as the link above — §24.3's
    #: verification notice, made something a person can act on when they read
    #: the mail on a different device from the one they registered on.
    #:
    #: A separate purpose rather than a second use of EMAIL_VERIFICATION,
    #: because the two have different threat models and therefore different
    #: rules: the link is 256 bits and unguessable, the code is one of a
    #: million and is defended by a short life and a hard attempt limit.
    #: Sharing a purpose would force one set of rules onto both.
    EMAIL_VERIFICATION_CODE = "EMAIL_VERIFICATION_CODE", "Email verification code"
    PASSWORD_RESET = "PASSWORD_RESET", "Password reset"


class OneTimeToken(TimestampedModel):
    """Email verification and password reset links. Designed; ADR 0007.

    Only the SHA-256 of the token is stored. The plaintext exists in the
    email and nowhere else, so a database disclosure does not hand over the
    ability to take over every pending account — the same reason a password
    is not stored either.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_tokens")
    purpose = models.CharField(max_length=32, choices=TokenPurpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True, default=None)

    #: Failed guesses against this row. Meaningless for a link — 256 bits are
    #: not guessed — and the whole defence for a six-digit code, which has a
    #: search space of a million and would otherwise fall to a script in
    #: minutes. The limit is a `system_setting`, so it is checked where the
    #: count is read rather than constrained here.
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "one_time_token"
        indexes = [
            models.Index(fields=["user", "purpose", "-created_at"], name="ott_user_purpose_idx")
        ]

    def __str__(self) -> str:
        return f"OneTimeToken({self.purpose})"

    def is_usable(self, *, now: Any) -> bool:
        return self.consumed_at is None and now < self.expires_at
