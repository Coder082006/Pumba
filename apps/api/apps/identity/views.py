"""identity module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). No business logic, no ORM queries.

Each view does four things and nothing else: validate the shape, call one
service, shape the response, and (for the web clients) manage the refresh
cookie of ADR 0008. Every decision they appear to make was made in `domain/`
and applied in `services.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.authentication import principal_from_request
from apps.common.envelope import success_envelope
from apps.common.errors import AuthenticationError, NotFoundError
from apps.common.permissions import IsAuthenticatedPrincipal
from apps.common.throttling import LoginEmailThrottle, LoginIpThrottle, RegistrationThrottle
from apps.identity import serializers as ser
from apps.identity import services
from apps.identity.selectors import list_devices_for_principal

__all__ = [
    "RegisterView",
    "VerifyEmailView",
    "VerifyEmailCodeView",
    "ResendVerificationView",
    "LoginView",
    "RefreshView",
    "LogoutView",
    "ForgotPasswordView",
    "ResetPasswordView",
    "MfaEnrolView",
    "MfaConfirmView",
    "MeView",
    "DeviceListCreateView",
    "DeviceDeleteView",
]

#: ADR 0008. Scoped to the auth routes so it is not attached to every request.
REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _client_ip(request: Request) -> str | None:
    """The peer address, taken from the socket, not from a header.

    `X-Forwarded-For` is client-controlled and trusting it would let anyone
    forge the address in their own audit trail and rate-limit bucket. The
    proxy's real-IP handling belongs in deployment configuration, not here.
    """
    return request.META.get("REMOTE_ADDR")


def _is_web_origin(request: Request) -> bool:
    """ADR 0008: the cookie is set only for the known portal origins."""
    origin = request.headers.get("Origin")
    if not origin:
        return False
    return origin in set(getattr(settings, "CORS_ALLOWED_ORIGINS", []))


def _attach_refresh_cookie(response: Response, request: Request, token: str) -> Response:
    if not _is_web_origin(request):
        return response
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path=REFRESH_COOKIE_PATH,
    )
    return response


def _tokens_payload(pair: Any) -> dict[str, Any]:
    return {
        "access_token": pair.access_token,
        "refresh_token": pair.refresh_token,
        "token_type": pair.token_type,
        "expires_in": pair.expires_in,
    }


def _user_payload(dto: Any) -> dict[str, Any]:
    profile = dto.profile
    return {
        "public_id": str(dto.public_id),
        "email": dto.email,
        "status": dto.status,
        "email_verified": dto.email_verified,
        "mfa_enrolled": dto.mfa_enrolled,
        "roles": sorted(dto.roles),
        "created_at": dto.created_at,
        "profile": (
            None
            if profile is None
            else {
                "public_id": str(profile.public_id),
                "first_name": profile.first_name,
                "last_name": profile.last_name,
                "nationality": profile.nationality,
                "locale": profile.locale,
                "preferred_currency": profile.preferred_currency,
                "marketing_opt_in": profile.marketing_opt_in,
            }
        ),
    }


class _PublicView(APIView):
    """Unauthenticated by design. Named so the URL-conf audit can see it."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]


class RegisterView(_PublicView):
    """SRS §9.4.1."""

    throttle_classes = [RegistrationThrottle]

    @extend_schema(
        request=ser.RegisterSerializer,
        responses={201: ser.UserSerializer},
        summary="Register a tourist account",
    )
    def post(self, request: Request) -> Response:
        payload = ser.RegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        dto = services.register_tourist(**payload.validated_data, ip=_client_ip(request))
        return Response(
            success_envelope({"user": _user_payload(dto), "verification_required": True}),
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(_PublicView):
    @extend_schema(request=ser.VerifyEmailSerializer, responses={200: ser.UserSerializer})
    def post(self, request: Request) -> Response:
        payload = ser.VerifyEmailSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        dto = services.verify_email(payload.validated_data["token"], ip=_client_ip(request))
        return Response(success_envelope({"user": _user_payload(dto)}))


class VerifyEmailCodeView(_PublicView):
    """The six digits, for §24.3's verification popup.

    A sibling route rather than a second field on `VerifyEmailView`, because
    the two carry different secrets with different rules and a single endpoint
    accepting either would need to explain in its own body which one it got.
    """

    @extend_schema(request=ser.VerifyEmailCodeSerializer, responses={200: ser.UserSerializer})
    def post(self, request: Request) -> Response:
        payload = ser.VerifyEmailCodeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        dto = services.verify_email_code(
            payload.validated_data["email"],
            payload.validated_data["code"],
            ip=_client_ip(request),
        )
        return Response(success_envelope({"user": _user_payload(dto)}))


class ResendVerificationView(_PublicView):
    """§24.4's "offers to resend verification", and the popup's Resend.

    **Always 202, whatever the address.** The same rule §24.5 states for
    password reset: an answer that depended on whether the account existed
    would let anyone enumerate the register one address at a time.
    """

    @extend_schema(request=ser.ResendVerificationSerializer, responses={202: None})
    def post(self, request: Request) -> Response:
        payload = ser.ResendVerificationSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.resend_verification(payload.validated_data["email"])
        return Response(
            success_envelope({"message": "If that address needs verifying, a code is on its way."}),
            status=status.HTTP_202_ACCEPTED,
        )


class LoginView(_PublicView):
    """SRS §9.4.2."""

    # Both halves of the §9.6 limit. The per-IP one alone is defeated by a
    # thousand addresses attacking one account; the per-email one alone is
    # defeated by one address attacking a thousand accounts.
    throttle_classes = [LoginIpThrottle, LoginEmailThrottle]

    @extend_schema(request=ser.LoginSerializer, responses={200: ser.SessionSerializer})
    def post(self, request: Request) -> Response:
        payload = ser.LoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = services.authenticate(
            email=payload.validated_data["email"],
            password=payload.validated_data["password"],
            mfa_code=payload.validated_data.get("mfa_code") or None,
            ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
        response = Response(
            success_envelope(
                {
                    **_tokens_payload(result.tokens),
                    "principal": {
                        "public_id": str(result.user.public_id),
                        "roles": sorted(result.roles),
                    },
                }
            )
        )
        return _attach_refresh_cookie(response, request, result.tokens.refresh_token)


class RefreshView(_PublicView):
    """ADR 0008: the token may arrive in the body or in the cookie."""

    @extend_schema(request=ser.RefreshSerializer, responses={200: ser.SessionSerializer})
    def post(self, request: Request) -> Response:
        payload = ser.RefreshSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        token = payload.validated_data.get("refresh_token") or request.COOKIES.get(REFRESH_COOKIE)
        if not token:
            raise AuthenticationError("No session token was supplied.")

        result = services.refresh_tokens(
            token,
            ip=_client_ip(request),
            user_agent=request.headers.get("User-Agent", ""),
        )
        response = Response(
            success_envelope(
                {
                    **_tokens_payload(result.tokens),
                    # The same shape login answers with. A client restoring a
                    # session after a reload needs to know whose it is before
                    # it can render anything role-dependent, and a second trip
                    # to /me for that would be a round trip on every page load.
                    "principal": {
                        "public_id": str(result.user.public_id),
                        "roles": sorted(result.roles),
                    },
                }
            )
        )
        return _attach_refresh_cookie(response, request, result.tokens.refresh_token)


class LogoutView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        services.logout(principal=principal, ip=_client_ip(request))

        response = Response(status=status.HTTP_204_NO_CONTENT)
        # Revoking server-side is not enough on its own: the browser would
        # keep sending a cookie the server no longer honours (ADR 0008).
        response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
        return response


class ForgotPasswordView(_PublicView):
    """§24.5: the response is identical whether or not the address exists."""

    throttle_classes = [RegistrationThrottle]

    @extend_schema(request=ser.ForgotPasswordSerializer, responses={202: None})
    def post(self, request: Request) -> Response:
        payload = ser.ForgotPasswordSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.request_password_reset(
            email=payload.validated_data["email"], ip=_client_ip(request)
        )
        return Response(
            success_envelope(
                {"message": "If that address has an account, a reset link is on its way."}
            ),
            status=status.HTTP_202_ACCEPTED,
        )


class ResetPasswordView(_PublicView):
    throttle_classes = [RegistrationThrottle]

    @extend_schema(request=ser.ResetPasswordSerializer, responses={204: None})
    def post(self, request: Request) -> Response:
        payload = ser.ResetPasswordSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.reset_password(
            token=payload.validated_data["token"],
            new_password=payload.validated_data["new_password"],
            ip=_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MfaEnrolView(APIView):
    """Authenticated by password, not by MFA — this is how MFA is obtained."""

    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(request=None, responses={200: None})
    def post(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        return Response(success_envelope(services.begin_mfa_enrolment(principal=principal)))


class MfaConfirmView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(request=ser.MfaConfirmSerializer, responses={204: None})
    def post(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        payload = ser.MfaConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.confirm_mfa_enrolment(
            principal=principal, code=payload.validated_data["code"], ip=_client_ip(request)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """The caller's own account.

    No `ownership_resource`: the row is selected *by* the principal rather
    than looked up by an identifier the caller supplies, so there is no
    identifier to get wrong. The URL-conf audit lists it for that reason.
    """

    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(responses={200: ser.UserSerializer})
    def get(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        dto = services.get_principal(user_id=principal.user_id)
        if dto is None:
            raise NotFoundError()
        from apps.identity.selectors import get_user_for_principal

        user = get_user_for_principal(principal, principal.user_public_id)
        if user is None:
            raise NotFoundError()
        return Response(success_envelope(_user_payload(user)))


class DeviceListCreateView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(responses={200: ser.DeviceSerializer(many=True)})
    def get(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        return Response(
            success_envelope(
                [
                    {
                        "public_id": str(d.public_id),
                        "platform": d.platform,
                        "device_name": d.device_name,
                        "last_seen_at": d.last_seen_at,
                    }
                    for d in list_devices_for_principal(principal)
                ]
            )
        )

    @extend_schema(request=ser.DeviceRegisterSerializer, responses={201: ser.DeviceSerializer})
    def post(self, request: Request) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        payload = ser.DeviceRegisterSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        dto = services.register_device(
            principal=principal, **payload.validated_data, ip=_client_ip(request)
        )
        return Response(
            success_envelope(
                {
                    "public_id": str(dto.public_id),  # type: ignore[attr-defined]
                    "platform": dto.platform,  # type: ignore[attr-defined]
                    "device_name": dto.device_name,  # type: ignore[attr-defined]
                    "last_seen_at": dto.last_seen_at,  # type: ignore[attr-defined]
                }
            ),
            status=status.HTTP_201_CREATED,
        )


class DeviceDeleteView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(responses={204: None})
    def delete(self, request: Request, public_id: UUID) -> Response:
        principal = principal_from_request(request)
        assert principal is not None
        if not services.remove_device(
            principal=principal, public_id=public_id, ip=_client_ip(request)
        ):
            # 404, not 403 — §30.3. The service looked the device up through
            # the scoped selector, so a foreign row was never loaded.
            raise NotFoundError()
        return Response(status=status.HTTP_204_NO_CONTENT)
