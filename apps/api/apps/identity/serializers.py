"""identity module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). Syntactic validation only.

Business rules live in `domain/` and are applied by `services.py`. What these
do is reject input that is the wrong *shape* — a missing field, a string
where a boolean belongs, a country code of the wrong length.

Every serializer sets `extra_kwargs`/explicit fields rather than `__all__`.
SRS §30.6: "unknown fields rejected rather than ignored" and "mass assignment
is prevented by explicit serializer field lists".
"""

from __future__ import annotations

from typing import Any, ClassVar

from rest_framework import serializers

from apps.common.serializers import StrictSerializer

__all__ = [
    "StrictSerializer",
    "RegisterSerializer",
    "VerifyEmailSerializer",
    "VerifyEmailCodeSerializer",
    "ResendVerificationSerializer",
    "LoginSerializer",
    "RefreshSerializer",
    "ForgotPasswordSerializer",
    "ResetPasswordSerializer",
    "MfaConfirmSerializer",
    "DeviceRegisterSerializer",
    "UserSerializer",
    "TokenPairSerializer",
    "PrincipalSerializer",
    "SessionSerializer",
]


class RegisterSerializer(StrictSerializer):
    """SRS §9.4.1."""

    email = serializers.EmailField(max_length=254)
    # No `min_length` here: length is a *business* rule from system_setting
    # (§30.2), applied in the domain layer, and duplicating it would give two
    # places to change it and one of them would be missed.
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(min_length=2, max_length=80)
    last_name = serializers.CharField(min_length=2, max_length=80)
    nationality = serializers.RegexField(r"^[A-Za-z]{2}$", required=False, allow_null=True)
    locale = serializers.CharField(max_length=10, required=False, default="en")
    preferred_currency = serializers.RegexField(r"^[A-Za-z]{3}$", required=False, default="USD")
    marketing_opt_in = serializers.BooleanField(required=False, default=False)

    def validate_nationality(self, value: str | None) -> str | None:
        return None if value is None else value.upper()

    def validate_preferred_currency(self, value: str) -> str:
        return value.upper()


class VerifyEmailSerializer(StrictSerializer):
    token = serializers.CharField(max_length=128, trim_whitespace=True)


class VerifyEmailCodeSerializer(StrictSerializer):
    """§24.3's popup: the address just registered, and the six digits emailed.

    The email is carried rather than inferred from a session, because there is
    no session yet — the account is PENDING and cannot sign in until this
    succeeds. Digits only, exactly six: a code that arrived with a space or a
    non-breaking hyphen from a mail client should be cleaned by the client, and
    anything else is not a code this service ever issued.
    """

    email = serializers.EmailField(max_length=254)
    code = serializers.RegexField(r"^\d{6}$", trim_whitespace=True)


class ResendVerificationSerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254)


class LoginSerializer(StrictSerializer):
    """SRS §9.4.2: `{ "email", "password", "mfa_code"? }`."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    mfa_code = serializers.CharField(
        required=False, allow_blank=True, max_length=10, write_only=True
    )


class RefreshSerializer(StrictSerializer):
    #: Optional: ADR 0008 lets the token arrive in the body *or* the cookie.
    refresh_token = serializers.CharField(required=False, allow_blank=True)


class ForgotPasswordSerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254)


class ResetPasswordSerializer(StrictSerializer):
    token = serializers.CharField(max_length=128)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class MfaConfirmSerializer(StrictSerializer):
    code = serializers.CharField(min_length=6, max_length=10)


class DeviceRegisterSerializer(StrictSerializer):
    platform = serializers.ChoiceField(choices=["IOS", "ANDROID", "WEB"])
    push_token = serializers.CharField(max_length=512)
    device_name = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Response shapes — declared so drf-spectacular documents them accurately.
# ---------------------------------------------------------------------------


class TouristProfileSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    nationality = serializers.CharField(read_only=True, allow_null=True)
    locale = serializers.CharField(read_only=True)
    preferred_currency = serializers.CharField(read_only=True)
    marketing_opt_in = serializers.BooleanField(read_only=True)


class UserSerializer(serializers.Serializer[Any]):
    """§9.1: `public_id` only. Never `id`, never a credential."""

    public_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    status = serializers.CharField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    mfa_enrolled = serializers.BooleanField(read_only=True)
    roles: ClassVar[Any] = serializers.ListField(child=serializers.CharField(), read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    profile = TouristProfileSerializer(read_only=True, allow_null=True)


class TokenPairSerializer(serializers.Serializer[Any]):
    access_token = serializers.CharField(read_only=True)
    refresh_token = serializers.CharField(read_only=True)
    token_type = serializers.CharField(read_only=True)
    expires_in = serializers.IntegerField(read_only=True)


class PrincipalSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField(read_only=True)
    roles = serializers.ListField(child=serializers.CharField(), read_only=True)


class SessionSerializer(TokenPairSerializer):
    """What `/auth/login` and `/auth/refresh` actually answer with.

    Both were documented as a bare `TokenPairSerializer` while login had been
    returning `principal` all along, so the committed specification understated
    the response and the generated client had no type for a field it was being
    sent. Refresh now returns it too — a client restoring a session after a
    reload has to know whose it is — so the two are described once, together.
    """

    principal = PrincipalSerializer(read_only=True)


class DeviceSerializer(serializers.Serializer[Any]):
    public_id = serializers.UUIDField(read_only=True)
    platform = serializers.CharField(read_only=True)
    device_name = serializers.CharField(read_only=True)
    last_seen_at = serializers.DateTimeField(read_only=True)
