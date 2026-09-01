"""identity module — SRS §6.4.

Interface layer (SRS §8.2 layer 1).

Route names are stable and namespaced (`v1:identity:login`), because the
authorisation-matrix test enumerates them and a renamed route should show up
as a matrix change rather than as a silently skipped endpoint.
"""

from django.urls import path

from apps.identity.views import (
    DeviceDeleteView,
    DeviceListCreateView,
    ForgotPasswordView,
    LoginView,
    LogoutView,
    MeView,
    MfaConfirmView,
    MfaEnrolView,
    RefreshView,
    RegisterView,
    ResendVerificationView,
    ResetPasswordView,
    VerifyEmailCodeView,
    VerifyEmailView,
)

app_name = "identity"

urlpatterns = [
    path("auth/register", RegisterView.as_view(), name="register"),
    path("auth/verify-email", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/verify-email/code", VerifyEmailCodeView.as_view(), name="verify-email-code"),
    path(
        "auth/verify-email/resend",
        ResendVerificationView.as_view(),
        name="verify-email-resend",
    ),
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/refresh", RefreshView.as_view(), name="refresh"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("auth/password/forgot", ForgotPasswordView.as_view(), name="password-forgot"),
    path("auth/password/reset", ResetPasswordView.as_view(), name="password-reset"),
    path("auth/mfa/enrol", MfaEnrolView.as_view(), name="mfa-enrol"),
    path("auth/mfa/verify", MfaConfirmView.as_view(), name="mfa-verify"),
    path("me", MeView.as_view(), name="me"),
    path("me/devices", DeviceListCreateView.as_view(), name="device-list"),
    path("me/devices/<uuid:public_id>", DeviceDeleteView.as_view(), name="device-detail"),
]
