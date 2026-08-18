"""Assertions about the project's own configuration.

These live here rather than under `apps/common/tests/` because they import
`config`, and the `common-is-a-leaf` contract forbids `apps.common` from
depending on it — import-linter caught exactly that. `tests/` is outside the
contract's root packages, which is the right home for a test *about* the
project rather than about a module.
"""

from __future__ import annotations

import pytest

from config.settings import base


class TestPasswordHashing:
    def test_the_project_configures_the_srs_argon2_hasher(self) -> None:
        """Read from `base`, not from the effective settings: `ci` swaps in
        MD5 so the suite is not spending 64 MiB of work per fixture, and
        asserting the effective value would only ever confirm that."""
        assert base.PASSWORD_HASHERS[0] == "apps.common.hashers.PlatformArgon2PasswordHasher"

    def test_no_weak_hasher_is_configured_for_real_environments(self) -> None:
        for path in base.PASSWORD_HASHERS:
            assert "MD5" not in path
            assert "SHA1" not in path
            assert "Unsalted" not in path


class TestAuthenticationWiring:
    def test_the_user_model_is_ours(self) -> None:
        assert base.AUTH_USER_MODEL == "identity.User"

    def test_drf_authenticates_with_the_principal_class(self) -> None:
        assert base.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == [
            "apps.common.authentication.PrincipalJWTAuthentication"
        ]

    def test_drf_is_closed_by_default(self) -> None:
        """A view that declares no permission classes must require a
        principal, so making a route public is a deliberate, visible act."""
        assert base.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
            "apps.common.permissions.IsAuthenticatedPrincipal"
        ]

    def test_session_middleware_is_absent(self) -> None:
        """The API is stateless and bearer-token only (SRS §30.4)."""
        joined = " ".join(base.MIDDLEWARE)
        assert "SessionMiddleware" not in joined
        assert "AuthenticationMiddleware" not in joined

    def test_auth_e003_is_silenced_for_the_stated_reason(self) -> None:
        """§7.5.1 requires a *partial* unique index on email; Django's check
        insists on an unconditional one. See config/settings/base.py."""
        assert "auth.E003" in base.SILENCED_SYSTEM_CHECKS


class TestTimeAndMoneyConventions:
    def test_timestamps_are_utc(self) -> None:
        """SRS §7.2: TIMESTAMPTZ stored in UTC."""
        assert base.USE_TZ is True
        assert base.TIME_ZONE == "UTC"

    def test_sequential_ids_are_big(self) -> None:
        assert base.DEFAULT_AUTO_FIELD == "django.db.models.BigAutoField"


class TestNoMlDependencyIsConfigured:
    """Brief rule 1: no AI/ML capability, and none in any manifest."""

    @pytest.mark.parametrize(
        "forbidden", ["torch", "tensorflow", "sklearn", "scikit", "transformers", "openai"]
    )
    def test_absent_from_installed_apps(self, forbidden: str) -> None:
        assert not any(forbidden in app.lower() for app in base.INSTALLED_APPS)
