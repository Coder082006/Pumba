"""Service-layer tests — the SRS §33.12 cases for Phase 2.

TC-001, 002, 003, 010, 011, 012, 013 are named in their test classes so a
failure names the acceptance criterion it breaks.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common import audit
from apps.common.audit import AuditRecord
from apps.common.authz import Role as RoleEnum
from apps.common.errors import AuthenticationError, ConflictError, ValidationError
from apps.common.ports_registry import get_breach_port, get_email_port, reset_ports
from apps.identity import repositories as repo
from apps.identity import services
from apps.identity.models import (
    Device,
    OneTimeToken,
    Session,
    User,
    UserStatus,
)

pytestmark = pytest.mark.django_db

VALID_PASSWORD = "a-perfectly-fine-passphrase"


@pytest.fixture(autouse=True)
def _fresh_ports():
    reset_ports()
    yield
    reset_ports()


@pytest.fixture(autouse=True)
def _recording_audit_sink():
    """Assert on the audit *port*, not on administration's table.

    identity is L0 and 6.4 gives it no dependency on administration, so
    reading audit_log from here would break deps-identity — and import-linter
    caught exactly that. Recording the port's calls tests what this module is
    actually responsible for: that it emits the events 37.2 requires.
    """
    original = audit.get_sink()
    recorded: list[AuditRecord] = []
    audit.register_sink(recorded.append)
    yield recorded
    audit._sink = original


@pytest.fixture
def email_port():
    return get_email_port()


def register(email: str = "alice@example.com", password: str = VALID_PASSWORD, **kw):  # type: ignore[no-untyped-def]
    return services.register_tourist(
        email=email, password=password, first_name="Alice", last_name="Muller", **kw
    )


def verified_user(email: str = "alice@example.com", password: str = VALID_PASSWORD) -> User:
    register(email=email, password=password)
    user = repo.find_user_by_email(email)
    assert user is not None
    repo.mark_email_verified(user, now=timezone.now())
    user.refresh_from_db()
    return user


def audit_actions(recorded: list[AuditRecord]) -> list[str]:
    return [str(r.action) for r in recorded]


class TestTc001Register:
    def test_creates_a_pending_user_and_a_profile(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        dto = register()
        assert dto.status == UserStatus.PENDING
        assert dto.profile is not None
        assert dto.profile.first_name == "Alice"

    def test_grants_the_tourist_role(self) -> None:
        assert register().roles == {str(RoleEnum.TOURIST)}

    def test_sends_a_verification_email(  # type: ignore[no-untyped-def]
        self, email_port, django_capture_on_commit_callbacks
    ) -> None:
        """Sent via transaction.on_commit: an email is not rollback-able, and
        a verification link for a user row that never existed is worse than a
        delayed one. The test has to let the commit hooks run."""
        with django_capture_on_commit_callbacks(execute=True):
            register()
        assert [m["recipient"] for m in email_port.sent] == ["alice@example.com"]

    def test_writes_an_audit_row(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        register()
        assert "user.register" in audit_actions(_recording_audit_sink)

    def test_the_dto_never_carries_a_credential(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        dto = register()
        assert not hasattr(dto, "password")
        assert not hasattr(dto, "mfa_secret")
        assert not hasattr(dto, "id")


class TestTc002DuplicateRegistration:
    def test_a_second_registration_conflicts(self) -> None:
        register()
        with pytest.raises(ConflictError) as exc:
            register()
        assert exc.value.code == "EMAIL_ALREADY_REGISTERED"

    def test_no_second_user_row_is_created(self) -> None:
        register()
        with pytest.raises(ConflictError):
            register()
        assert User.objects.count() == 1

    def test_a_differently_cased_address_is_the_same_account(self) -> None:
        register(email="Alice@Example.com")
        with pytest.raises(ConflictError):
            register(email="alice@EXAMPLE.COM")


class TestTc003WeakPassword:
    def test_a_breached_password_is_refused(self) -> None:
        with pytest.raises(ValidationError) as exc:
            register(password="password1234")
        assert any(d["code"] == "BREACHED" for d in exc.value.details)

    def test_no_user_is_created(self) -> None:
        with pytest.raises(ValidationError):
            register(password="password1234")
        assert User.objects.count() == 0

    def test_a_short_password_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            register(password="short")

    def test_every_violation_is_reported_not_just_the_first(self) -> None:
        """§24.3 maps errors to fields by details[].field."""
        with pytest.raises(ValidationError) as exc:
            register(email="password@example.com", password="password")
        codes = {d["code"] for d in exc.value.details}
        assert {"TOO_SHORT", "EQUALS_EMAIL_LOCAL_PART"} <= codes


class TestBreachCheckFailurePolicy:
    """ADR 0006 — closed on registration, open on login."""

    def test_registration_fails_closed_when_the_corpus_is_down(self) -> None:
        get_breach_port().unavailable = True
        with pytest.raises(ValidationError) as exc:
            register()
        assert exc.value.code == "BREACH_CHECK_UNAVAILABLE"
        assert User.objects.count() == 0

    def test_login_fails_open_when_the_corpus_is_down(self) -> None:
        verified_user()
        get_breach_port().unavailable = True
        result = services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert result.tokens.access_token


class TestEmailVerification:
    def test_a_valid_token_activates_the_account(self, django_capture_on_commit_callbacks) -> None:  # type: ignore[no-untyped-def]
        raw = _register_and_capture(django_capture_on_commit_callbacks)
        dto = services.verify_email(raw)
        assert dto.status == UserStatus.ACTIVE
        assert dto.email_verified

    def test_a_token_cannot_be_used_twice(self, django_capture_on_commit_callbacks) -> None:  # type: ignore[no-untyped-def]
        raw = _register_and_capture(django_capture_on_commit_callbacks)
        services.verify_email(raw)
        with pytest.raises(ValidationError):
            services.verify_email(raw)

    def test_an_unknown_token_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            services.verify_email("not-a-real-token")

    def test_an_expired_token_is_refused(self, django_capture_on_commit_callbacks) -> None:  # type: ignore[no-untyped-def]
        raw = _register_and_capture(django_capture_on_commit_callbacks)
        OneTimeToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(ValidationError):
            services.verify_email(raw)


def _register_and_capture(capture) -> str:  # type: ignore[no-untyped-def]
    with capture(execute=True):
        register()
    return _verification_link_token(get_email_port())


def _verification_link_token(port) -> str:  # type: ignore[no-untyped-def]
    """The **link** token out of the verification email.

    Read from the `href`, not from `<code>`. That email now carries two secrets
    — six typeable digits in the `<code>` element and the 256-bit link beside
    them — and this feeds `verify_email`, which is the link's endpoint. When
    the code arrived, a helper scraping `<code>` silently started handing over
    the wrong one. `test_verification_code.py` covers the digits.
    """
    body = port.sent[-1]["html_body"]
    return body.split("?token=")[1].split('"')[0]


def _from_body(port) -> str:  # type: ignore[no-untyped-def]
    """The token out of a `<code>` element — the password-reset mail's shape."""
    body = port.sent[-1]["html_body"]
    return body.split("<code>")[1].split("</code>")[0]


class TestTc010LoginSuccess:
    def test_returns_both_tokens(self) -> None:
        verified_user()
        result = services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert result.tokens.access_token
        assert result.tokens.refresh_token
        assert result.tokens.token_type == "Bearer"

    def test_sets_last_login_at(self) -> None:
        user = verified_user()
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        user.refresh_from_db()
        assert user.last_login is not None

    def test_creates_a_session_row(self) -> None:
        verified_user()
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD, ip="203.0.113.7")
        session = Session.objects.get()
        assert session.is_live
        assert session.ip == "203.0.113.7"

    def test_writes_an_audit_row(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert "user.login" in audit_actions(_recording_audit_sink)


class TestTc011WrongPassword:
    def test_is_refused(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        with pytest.raises(AuthenticationError) as exc:
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")
        assert exc.value.code == "INVALID_CREDENTIALS"

    def test_increments_the_counter(self) -> None:
        user = verified_user()
        with pytest.raises(AuthenticationError):
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")
        user.refresh_from_db()
        assert user.failed_login_count == 1

    def test_writes_an_audit_row(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        with pytest.raises(AuthenticationError):
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")
        assert "user.login_failed" in audit_actions(_recording_audit_sink)


class TestTc012Lockout:
    def _fail(self, times: int) -> None:
        for _ in range(times):
            with pytest.raises(AuthenticationError):
                services.authenticate(email="alice@example.com", password="wrong-but-long-enough")

    def test_the_tenth_failure_locks_the_account(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        user = verified_user()
        self._fail(10)
        user.refresh_from_db()
        assert user.locked_until is not None

    def test_the_eleventh_attempt_is_refused_with_423(self) -> None:
        verified_user()
        self._fail(10)
        with pytest.raises(services.AccountLockedError) as exc:
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert exc.value.status_code == 423

    def test_the_correct_password_does_not_open_a_locked_account(self) -> None:
        verified_user()
        self._fail(10)
        with pytest.raises(services.AccountLockedError):
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_the_owner_is_notified(self, email_port) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        self._fail(10)
        assert any(m["template_id"] == "account_locked" for m in email_port.sent)

    def test_the_response_carries_the_unlock_time(self) -> None:
        verified_user()
        self._fail(10)
        with pytest.raises(services.AccountLockedError) as exc:
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert exc.value.details[0]["retry_after_seconds"] > 0

    def test_writes_an_audit_row(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        self._fail(10)
        assert "user.locked" in audit_actions(_recording_audit_sink)


class TestTc013NonEnumeration:
    def test_an_unknown_email_produces_the_same_error_as_a_wrong_password(
        self, _recording_audit_sink
    ) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        with pytest.raises(AuthenticationError) as unknown:
            services.authenticate(email="nobody@example.com", password=VALID_PASSWORD)
        with pytest.raises(AuthenticationError) as wrong:
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")

        assert unknown.value.code == wrong.value.code
        assert str(unknown.value) == str(wrong.value)
        assert unknown.value.status_code == wrong.value.status_code

    def test_the_password_is_verified_before_any_account_state_check(self) -> None:
        """A suspended account must not be distinguishable from a nonexistent
        one without the password."""
        user = verified_user()
        user.status = UserStatus.SUSPENDED
        user.save(update_fields=["status"])

        with pytest.raises(AuthenticationError) as wrong_password:
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")
        assert wrong_password.value.code == "INVALID_CREDENTIALS"

        with pytest.raises(services.AccountSuspendedError):
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_an_unverified_account_is_only_revealed_to_the_password_holder(self) -> None:
        register()
        with pytest.raises(AuthenticationError) as wrong:
            services.authenticate(email="alice@example.com", password="wrong-but-long-enough")
        assert wrong.value.code == "INVALID_CREDENTIALS"

        with pytest.raises(services.EmailNotVerifiedError):
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_password_reset_says_nothing_about_existence(self, email_port) -> None:  # type: ignore[no-untyped-def]
        """§24.5 — identical outcome, and nothing to branch on."""
        assert services.request_password_reset(email="nobody@example.com") is None
        assert email_port.sent == []


class TestRefreshRotation:
    def _login(self):  # type: ignore[no-untyped-def]
        verified_user()
        return services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_a_valid_refresh_rotates(self) -> None:
        first = self._login()
        second = services.refresh_tokens(first.tokens.refresh_token)
        assert second.tokens.refresh_token != first.tokens.refresh_token
        assert Session.objects.count() == 2

    def test_a_refresh_says_whose_session_it_is(self) -> None:
        """Login and refresh both establish a session, so both answer with the
        principal. ADR 0008 keeps the access token in the browser's memory, so
        refresh is the call that restores a session after a reload — and a
        client handed only tokens would need a second trip to `/me` before it
        could render anything role-dependent."""
        first = self._login()
        second = services.refresh_tokens(first.tokens.refresh_token)
        assert second.user.public_id == first.user.public_id
        assert second.roles == first.roles

    def test_the_predecessor_is_marked_superseded(self) -> None:
        first = self._login()
        services.refresh_tokens(first.tokens.refresh_token)
        original = Session.objects.order_by("id").first()
        assert original is not None
        assert original.superseded_by is not None

    def test_the_family_is_preserved_across_rotation(self) -> None:
        first = self._login()
        services.refresh_tokens(first.tokens.refresh_token)
        assert Session.objects.values_list("family_id", flat=True).distinct().count() == 1


class TestReuseDetection:
    """§30.2: "a replayed refresh token revokes the whole family and alerts
    the user"."""

    def _login(self):  # type: ignore[no-untyped-def]
        verified_user()
        return services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_replaying_a_rotated_token_is_refused(self) -> None:
        first = self._login()
        services.refresh_tokens(first.tokens.refresh_token)
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(first.tokens.refresh_token)

    def test_replay_revokes_every_session_in_the_family(self) -> None:
        first = self._login()
        second = services.refresh_tokens(first.tokens.refresh_token)
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(first.tokens.refresh_token)

        assert Session.objects.filter(revoked_at__isnull=True).count() == 0
        # The successor the attacker did not have is dead too.
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(second.tokens.refresh_token)

    def test_replay_alerts_the_owner(self, email_port) -> None:  # type: ignore[no-untyped-def]
        first = self._login()
        services.refresh_tokens(first.tokens.refresh_token)
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(first.tokens.refresh_token)
        assert any(m["template_id"] == "session_reuse_detected" for m in email_port.sent)

    def test_replay_writes_the_audit_row(self, _recording_audit_sink) -> None:  # type: ignore[no-untyped-def]
        first = self._login()
        services.refresh_tokens(first.tokens.refresh_token)
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(first.tokens.refresh_token)
        assert "token.reuse_detected" in audit_actions(_recording_audit_sink)

    def test_a_garbage_token_is_refused_without_revoking_anything(
        self, _recording_audit_sink
    ) -> None:  # type: ignore[no-untyped-def]
        self._login()
        with pytest.raises(AuthenticationError):
            services.refresh_tokens("not-a-jwt")
        assert Session.objects.filter(revoked_at__isnull=True).count() == 1


class TestLogout:
    def test_revokes_every_session(self) -> None:
        user = verified_user()
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        principal = repo.principal_for(user)

        assert services.logout(principal=principal) == 2
        assert Session.objects.filter(revoked_at__isnull=True).count() == 0

    def test_a_revoked_session_cannot_refresh(self) -> None:
        user = verified_user()
        result = services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        services.logout(principal=repo.principal_for(user))
        with pytest.raises(AuthenticationError):
            services.refresh_tokens(result.tokens.refresh_token)


class TestPasswordReset:
    def test_the_full_flow_changes_the_password(self, email_port) -> None:  # type: ignore[no-untyped-def]
        verified_user()
        services.request_password_reset(email="alice@example.com")
        raw = _from_body(get_email_port())

        services.reset_password(token=raw, new_password="a-brand-new-passphrase")
        result = services.authenticate(email="alice@example.com", password="a-brand-new-passphrase")
        assert result.tokens.access_token

    def test_the_old_password_stops_working(self) -> None:
        verified_user()
        services.request_password_reset(email="alice@example.com")
        services.reset_password(
            token=_from_body(get_email_port()), new_password="a-brand-new-passphrase"
        )
        with pytest.raises(AuthenticationError):
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_resetting_ends_every_existing_session(self) -> None:
        """Whoever prompted the reset may already hold one."""
        verified_user()
        services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        services.request_password_reset(email="alice@example.com")
        services.reset_password(
            token=_from_body(get_email_port()), new_password="a-brand-new-passphrase"
        )
        assert Session.objects.filter(revoked_at__isnull=True).count() == 0

    def test_a_second_request_invalidates_the_first_link(self) -> None:
        """An attacker who triggered a reset must not keep a live link."""
        verified_user()
        services.request_password_reset(email="alice@example.com")
        first = _from_body(get_email_port())
        services.request_password_reset(email="alice@example.com")

        with pytest.raises(ValidationError):
            services.reset_password(token=first, new_password="a-brand-new-passphrase")

    def test_the_new_password_must_pass_the_policy(self) -> None:
        verified_user()
        services.request_password_reset(email="alice@example.com")
        with pytest.raises(ValidationError):
            services.reset_password(token=_from_body(get_email_port()), new_password="password1234")


class TestMfaEnrolment:
    def _principal(self, user: User):  # type: ignore[no-untyped-def]
        return repo.principal_for(user)

    def test_enrolment_returns_a_scannable_uri(self) -> None:
        user = verified_user()
        payload = services.begin_mfa_enrolment(principal=self._principal(user))
        assert payload["otpauth_uri"].startswith("otpauth://totp/")
        assert payload["secret"]

    def test_the_secret_is_stored_encrypted(self) -> None:
        user = verified_user()
        payload = services.begin_mfa_enrolment(principal=self._principal(user))
        user.refresh_from_db()
        assert payload["secret"].encode() not in bytes(user.mfa_secret)

    def test_enrolment_is_incomplete_until_a_code_is_verified(self) -> None:
        """An abandoned enrolment must not lock the user out."""
        user = verified_user()
        services.begin_mfa_enrolment(principal=self._principal(user))
        user.refresh_from_db()
        assert user.mfa_enrolled_at is None
        assert not user.has_mfa

    def test_confirming_with_a_valid_code_completes_it(  # type: ignore[no-untyped-def]
        self, _recording_audit_sink
    ) -> None:
        from apps.identity.domain.mfa import base32_to_secret, totp_code

        user = verified_user()
        payload = services.begin_mfa_enrolment(principal=self._principal(user))
        code = totp_code(base32_to_secret(payload["secret"]), at=timezone.now())

        services.confirm_mfa_enrolment(principal=self._principal(user), code=code)
        user.refresh_from_db()
        assert user.has_mfa
        assert "user.mfa_enrolled" in audit_actions(_recording_audit_sink)

    def test_confirming_with_a_wrong_code_is_refused(self) -> None:
        user = verified_user()
        services.begin_mfa_enrolment(principal=self._principal(user))
        with pytest.raises(ValidationError):
            services.confirm_mfa_enrolment(principal=self._principal(user), code="000000")


class TestMfaIsMandatoryForStaff:
    """§30.2 — "a provider account without TOTP must not reach the console"."""

    def _provider(self) -> User:
        user = verified_user()
        repo.grant_role(user, RoleEnum.PROVIDER_OWNER)
        return user

    def test_a_provider_without_enrolment_cannot_sign_in(self) -> None:
        self._provider()
        with pytest.raises(services.MfaRequiredError) as exc:
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)
        assert exc.value.details[0]["code"] == "MFA_ENROLMENT_REQUIRED"

    def test_a_provider_with_enrolment_must_supply_a_code(self) -> None:
        from apps.identity.domain.mfa import base32_to_secret, totp_code

        user = self._provider()
        payload = services.begin_mfa_enrolment(principal=repo.principal_for(user))
        code = totp_code(base32_to_secret(payload["secret"]), at=timezone.now())
        services.confirm_mfa_enrolment(principal=repo.principal_for(user), code=code)

        with pytest.raises(services.MfaRequiredError):
            services.authenticate(email="alice@example.com", password=VALID_PASSWORD)

    def test_a_provider_with_a_valid_code_signs_in(self) -> None:
        from apps.identity.domain.mfa import base32_to_secret, totp_code

        user = self._provider()
        payload = services.begin_mfa_enrolment(principal=repo.principal_for(user))
        secret = base32_to_secret(payload["secret"])
        services.confirm_mfa_enrolment(
            principal=repo.principal_for(user), code=totp_code(secret, at=timezone.now())
        )

        result = services.authenticate(
            email="alice@example.com",
            password=VALID_PASSWORD,
            mfa_code=totp_code(secret, at=timezone.now()),
        )
        assert result.tokens.access_token

    def test_a_tourist_needs_no_code(self) -> None:
        verified_user()
        assert services.authenticate(
            email="alice@example.com", password=VALID_PASSWORD
        ).tokens.access_token


class TestDevices:
    def test_registering_is_idempotent_by_token(self) -> None:
        user = verified_user()
        principal = repo.principal_for(user)
        services.register_device(principal=principal, platform="IOS", push_token="tok-1")
        services.register_device(principal=principal, platform="IOS", push_token="tok-1")
        assert Device.objects.filter(revoked_at__isnull=True).count() == 1

    def test_a_token_moving_to_another_account_revokes_the_old_row(self) -> None:
        """A handset that changed hands must stop receiving the previous
        owner's itinerary."""
        first = verified_user()
        second = verified_user(email="bob@example.com")

        services.register_device(
            principal=repo.principal_for(first), platform="IOS", push_token="shared"
        )
        services.register_device(
            principal=repo.principal_for(second), platform="IOS", push_token="shared"
        )

        live = Device.objects.filter(revoked_at__isnull=True)
        assert live.count() == 1
        assert live.get().user_id == second.pk

    def test_a_foreign_device_cannot_be_removed(self) -> None:
        first = verified_user()
        second = verified_user(email="bob@example.com")
        dto = services.register_device(
            principal=repo.principal_for(first), platform="IOS", push_token="tok-1"
        )

        assert (
            services.remove_device(
                principal=repo.principal_for(second),
                public_id=dto.public_id,  # type: ignore[attr-defined]
            )
            is False
        )
        assert Device.objects.filter(revoked_at__isnull=True).count() == 1

    def test_an_unknown_device_is_indistinguishable_from_a_foreign_one(self) -> None:
        user = verified_user()
        assert (
            services.remove_device(principal=repo.principal_for(user), public_id=uuid.uuid4())
            is False
        )


class TestGetPrincipal:
    def test_builds_the_principal_from_a_user_id(self) -> None:
        user = verified_user()
        principal = services.get_principal(user_id=user.pk)
        assert principal is not None
        assert principal.user_public_id == user.public_id
        assert RoleEnum.TOURIST in principal.roles

    def test_an_unknown_user_yields_none(self) -> None:
        assert services.get_principal(user_id=999_999) is None
