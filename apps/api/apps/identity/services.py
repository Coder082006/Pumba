"""identity module — SRS §6.4.

Owns:
        user, role, user_role, tourist_profile, session, device

Interface:  authenticate(), issue_tokens(), get_principal()
Depends on: —
Layer:      L0

Application layer (SRS §8.2 layer 2).

The ONLY module boundary. Other modules call this and nothing else
(SRS §6.5 rule 1). Orchestrates a use case in one transaction and
emits domain events.

Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

Every decision in here is made by a pure function in `domain/`; this layer
supplies the clock, the database and the ports, and decides what to do with
the answer. That is why the file reads as a sequence of decisions rather than
a sequence of queries.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.common.audit import AuditAction, record_audit
from apps.common.authz import Principal, mfa_mandatory
from apps.common.config import get_setting
from apps.common.errors import (
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from apps.common.ports_registry import get_breach_port, get_crypto_port, get_email_port
from apps.identity import repositories as repo
from apps.identity.domain.lockout import LockoutPolicy, is_locked, register_failure, remaining
from apps.identity.domain.mfa import provisioning_uri, secret_to_base32, verify_totp
from apps.identity.domain.passwords import validate_password
from apps.identity.domain.tokens import FamilyAction, RefusalReason, TokenView, evaluate_refresh
from apps.identity.dto import LoginResult, TokenPair, UserDTO
from apps.identity.models import TokenPurpose, User, UserStatus
from apps.identity.selectors import to_device_dto, to_user_dto
from ports.breach import BreachLookupError, password_prefix, password_suffix

logger = logging.getLogger(__name__)

__all__ = [
    "AccountLockedError",
    "EmailNotVerifiedError",
    "AccountSuspendedError",
    "MfaRequiredError",
    "register_tourist",
    "verify_email",
    "verify_email_code",
    "resend_verification",
    "authenticate",
    "refresh_tokens",
    "logout",
    "request_password_reset",
    "reset_password",
    "begin_mfa_enrolment",
    "confirm_mfa_enrolment",
    "register_device",
    "remove_device",
    "get_principal",
]

#: A precomputed hash of a value nobody can supply. Verifying against it for
#: an unknown email makes the Argon2 work factor part of *every* login, so the
#: timing of "no such account" and "wrong password" overlap — TC-013.
_DUMMY_PASSWORD_HASH = make_password("dummy-password-for-constant-time-comparison")


class AccountLockedError(AuthenticationError):
    """§9.4.2: 423 after the configured failure threshold."""

    status_code = 423
    code = "ACCOUNT_LOCKED"
    default_message = "This account is temporarily locked."


class EmailNotVerifiedError(PermissionDeniedError):
    code = "EMAIL_NOT_VERIFIED"
    default_message = "Verify your email address before continuing."


class AccountSuspendedError(PermissionDeniedError):
    code = "ACCOUNT_SUSPENDED"
    default_message = "This account is suspended."


class MfaRequiredError(AuthenticationError):
    code = "MFA_REQUIRED"
    default_message = "A one-time code is required."


class InvalidCredentialsError(AuthenticationError):
    code = "INVALID_CREDENTIALS"
    default_message = "Those credentials are not valid."


@dataclass(frozen=True, slots=True)
class _Issued:
    pair: TokenPair
    jti: uuid.UUID
    family_id: uuid.UUID


def _now() -> datetime:
    return timezone.now()


def _lockout_policy() -> LockoutPolicy:
    return LockoutPolicy(
        threshold=int(get_setting("auth.lockout.threshold")),
        window=timedelta(minutes=int(get_setting("auth.lockout.window_minutes"))),
        base_duration=timedelta(minutes=int(get_setting("auth.lockout.base_minutes"))),
        max_duration=timedelta(minutes=int(get_setting("auth.lockout.max_minutes"))),
    )


# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------


def _is_breached(password: str, *, fail_closed: bool) -> bool:
    """Consult the breach corpus, applying the failure policy of ADR 0006.

    Registration and reset **fail closed** — refuse to set a password we
    could not check; the user retries in a minute and loses nothing. Login
    **fails open** — never deny an existing user their own account because a
    third party is down.
    """
    if not get_setting("auth.password.breach_check_enabled"):
        return False
    try:
        suffixes = get_breach_port().suffixes_for_prefix(password_prefix(password))
    except BreachLookupError:
        if fail_closed:
            raise ValidationError(
                "Could not verify this password against the breach list. Please try again.",
                code="BREACH_CHECK_UNAVAILABLE",
            ) from None
        return False
    return password_suffix(password) in suffixes


def _validate_new_password(password: str, *, email: str) -> None:
    violations = validate_password(
        password,
        email=email,
        min_length=int(get_setting("auth.password.min_length")),
        is_breached=_is_breached(password, fail_closed=True),
    )
    if violations:
        raise ValidationError(
            violations[0].message,
            details=[
                {"field": "password", "code": str(v.code), "message": v.message} for v in violations
            ],
        )


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _issue_tokens(
    user: User, *, family_id: uuid.UUID | None = None, mfa_satisfied: bool = False
) -> _Issued:
    """`issue_tokens()` from the §6.4 interface.

    Built on SimpleJWT so the signing and verification are not hand-rolled;
    the *family* bookkeeping is ours, because SimpleJWT's blacklist rotates
    tokens without the reuse detection §30.2 requires.
    """
    from rest_framework_simplejwt.tokens import RefreshToken

    access_minutes = int(get_setting("auth.access_token_minutes"))
    refresh_days = int(get_setting("auth.refresh_token_days"))

    # `for_user` is annotated as returning the `Token` base class in the
    # stubs; it returns the concrete `RefreshToken`, which is what carries
    # `.access_token`. Two narrow ignores rather than a cast, so the day the
    # stubs are fixed they show up as unused (warn_unused_ignores is on).
    refresh = RefreshToken.for_user(user)
    # Both lifetimes come from the register rather than from SIMPLE_JWT, so
    # the JWT's own exp and the session row's expires_at cannot disagree —
    # and an administrator can shorten either without a deployment.
    refresh.set_exp(lifetime=timedelta(days=refresh_days))
    jti = uuid.UUID(str(refresh["jti"]))
    family = family_id or uuid.uuid4()
    refresh["family_id"] = str(family)
    refresh["mfa"] = mfa_satisfied

    access = refresh.access_token  # type: ignore[attr-defined]
    access.set_exp(lifetime=timedelta(minutes=access_minutes))
    # Carried on the access token so every later request knows whether the
    # MFA obligation was met for *this* session, without a database read.
    access["mfa"] = mfa_satisfied

    return _Issued(
        pair=TokenPair(
            access_token=str(access),
            refresh_token=str(refresh),
            expires_in=access_minutes * 60,
        ),
        jti=jti,
        family_id=family,
    )


def _persist_session(user: User, issued: _Issued, *, ip: str | None, user_agent: str) -> None:
    repo.create_session(
        user,
        jti=issued.jti,
        family_id=issued.family_id,
        expires_at=_now() + timedelta(days=int(get_setting("auth.refresh_token_days"))),
        ip=ip,
        user_agent=user_agent,
    )


# ---------------------------------------------------------------------------
# Registration and verification
# ---------------------------------------------------------------------------


@transaction.atomic
def register_tourist(
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    nationality: str | None = None,
    locale: str = "en",
    preferred_currency: str = "USD",
    marketing_opt_in: bool = False,
    ip: str | None = None,
) -> UserDTO:
    """SRS §9.4.1. TC-001, TC-002, TC-003."""
    _validate_new_password(password, email=email)

    if repo.find_user_by_email(email) is not None:
        # §9.4.1 specifies 409 here. It is enumerable by design — a
        # registration form that cannot say "this address is already
        # registered" is unusable, and §24.3 requires exactly that message
        # with a login link. The non-enumeration requirement (TC-013) is
        # scoped to login and password reset, where there is no such excuse.
        raise ConflictError(
            "That email address is already registered.", code="EMAIL_ALREADY_REGISTERED"
        )

    user = repo.create_tourist(
        email=email,
        password_hash=make_password(password),
        first_name=first_name,
        last_name=last_name,
        nationality=nationality,
        locale=locale,
        preferred_currency=preferred_currency,
        marketing_opt_in=marketing_opt_in,
    )

    _send_verification_email(user)
    record_audit(
        AuditAction.USER_REGISTER,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
        after={"status": user.status, "email": user.email},
    )
    return to_user_dto(user)


def _send_verification_email(user: User, *, base_url: str | None = None) -> None:
    """One email, two secrets, because they answer different situations.

    The **link** is 256 bits and lasts a day: it is for somebody reading the
    mail on the device they registered on, who taps it and is done. The
    **code** is six digits and lasts minutes: it is for somebody who
    registered on a laptop and reads the mail on a phone, and who otherwise has
    to retype forty characters of base64 or give up.

    They are separate rows with separate rules rather than one secret used two
    ways, because a million-value code with a link's lifetime and no attempt
    limit is a lock anyone can pick, and a 256-bit link expiring in fifteen
    minutes is an inconvenience with no security in return.
    """
    now = _now()
    raw = repo.issue_one_time_token(
        user,
        TokenPurpose.EMAIL_VERIFICATION,
        ttl=timedelta(hours=int(get_setting("auth.email_verification_ttl_hours"))),
        now=now,
    )
    code = repo.issue_verification_code(
        user,
        ttl=timedelta(minutes=int(get_setting("auth.email_verification_code_ttl_minutes"))),
        now=now,
    )
    minutes = int(get_setting("auth.email_verification_code_ttl_minutes"))
    link = f"{base_url or get_setting('web.tourist_base_url')}/verify-email?token={raw}"

    # Sent after the transaction commits: an email is not rollback-able, and
    # a verification link for a user row that never existed is worse than a
    # delayed one.
    transaction.on_commit(
        lambda: get_email_port().send(
            to=user.email,
            subject="Verify your email address",
            html_body=(
                f"<p>Your verification code is <code>{code}</code>. "
                f"It expires in {minutes} minutes.</p>"
                f'<p>Or open <a href="{link}">this link</a>.</p>'
            ),
            template_id="email_verification",
            context={"code": code, "token": raw, "url": link},
        )
    )


@transaction.atomic
def verify_email(token: str, *, ip: str | None = None) -> UserDTO:
    user = repo.consume_one_time_token(token, TokenPurpose.EMAIL_VERIFICATION, now=_now())
    if user is None:
        raise ValidationError("That verification link is invalid or has expired.")

    repo.mark_email_verified(user, now=_now())
    record_audit(
        AuditAction.USER_EMAIL_VERIFIED,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
        after={"status": user.status},
    )
    return to_user_dto(user)


def verify_email_code(email: str, code: str, *, ip: str | None = None) -> UserDTO:
    """§24.3's verification notice, as six digits a person can type.

    **Deliberately not `@transaction.atomic`, and this is the whole security
    of the thing.** It was, and the attempt limit enforced nothing: the
    counter is incremented on a wrong guess and the wrong guess then raises,
    so the rollback undid the count. Five failures left `attempts` at zero and
    the code still live — a million-value secret with unlimited guesses. The
    increment has to *commit* even though the request fails, which means the
    failure cannot be inside the transaction that wrote it.

    `consume_verification_code` opens its own transaction for the read, the
    compare and the count, which is the part that must be atomic. Marking the
    account verified and writing the audit entry is a second transaction
    below, because those two must land together or not at all.

    **One message for every failure, and it is deliberate.** Wrong code,
    expired code, too many attempts, no such account — all of them raise the
    same `ValidationError`. §30.3 asks that absence and inaccessibility be
    indistinguishable, and the same reasoning applies here: an error that
    distinguished "no such account" from "wrong code" would turn this endpoint
    into a register of who has signed up, and one that distinguished "expired"
    from "wrong" would tell an attacker whether to keep guessing.

    The repository still separates the outcomes, because the *server* needs
    them — to decide whether to burn the row, and so an operator can see a
    brute-force attempt in the logs rather than a wall of identical failures.

    An already-verified account raises too. Nothing is wrong with the caller,
    but the alternative is answering "fine" to a code that was never checked.
    """
    user = repo.find_user_by_email(email)
    if user is None or user.email_verified_at is not None:
        # The work of a real check is not done, which is a timing signal. It is
        # accepted: the rate limit in §9.6 bounds how often this can be asked,
        # and the alternative — verifying against a dummy row — is a second
        # code path that must stay in step with the first for ever.
        raise ValidationError("That code is invalid or has expired.")

    outcome = repo.consume_verification_code(
        user,
        code,
        now=_now(),
        max_attempts=int(get_setting("auth.email_verification_code_max_attempts")),
    )
    if outcome is not repo.CodeOutcome.OK:
        logger.info(
            "email_verification_code_rejected",
            extra={"outcome": outcome.value, "user_id": user.pk},
        )
        raise ValidationError("That code is invalid or has expired.")

    with transaction.atomic():
        repo.mark_email_verified(user, now=_now())
        record_audit(
            AuditAction.USER_EMAIL_VERIFIED,
            entity_type="user",
            entity_id=str(user.public_id),
            actor_user_id=user.pk,
            ip=ip,
            after={"status": user.status},
        )
    return to_user_dto(user)


def resend_verification(email: str) -> None:
    """§24.4's "offers to resend verification", and §24.3's Resend button.

    **Returns nothing, and succeeds for an address that does not exist.** The
    same rule §24.5 states for password reset: a response that differed would
    let anyone test whether an address has an account here, one request at a
    time. An already-verified account is also a silent no-op — telling the
    caller would answer the same question.
    """
    user = repo.find_user_by_email(email)
    if user is None or user.email_verified_at is not None:
        return
    _send_verification_email(user)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def authenticate(
    *,
    email: str,
    password: str,
    mfa_code: str | None = None,
    ip: str | None = None,
    user_agent: str = "",
) -> LoginResult:
    """SRS §9.4.2. TC-010 to TC-013.

    The ordering here is deliberate and is the whole of TC-013. The password
    is verified *before* any account-state check, and an unknown email is
    verified against a dummy hash, so the work done and the errors raised do
    not depend on whether the address exists.
    """
    now = _now()
    user = repo.find_user_by_email(email)

    if user is None:
        # Pay the same Argon2 cost, then fail identically.
        check_password(password, _DUMMY_PASSWORD_HASH)
        record_audit(
            AuditAction.USER_LOGIN_FAILED,
            entity_type="user",
            ip=ip,
            reason="unknown_email",
        )
        raise InvalidCredentialsError()

    if is_locked(locked_until=user.locked_until, now=now):
        raise AccountLockedError(
            "This account is temporarily locked.",
            details=[
                {
                    "retry_after_seconds": int(
                        remaining(locked_until=user.locked_until, now=now).total_seconds()
                    )
                }
            ],
        )

    if not check_password(password, user.password):
        _register_failed_attempt(user, now=now, ip=ip)
        raise InvalidCredentialsError()

    # Only now does the account's own state matter. Checking it earlier would
    # let an attacker distinguish a suspended account from a nonexistent one
    # without knowing the password.
    if user.status == UserStatus.SUSPENDED:
        raise AccountSuspendedError()
    if not user.is_email_verified:
        raise EmailNotVerifiedError()
    if user.status == UserStatus.CLOSED:
        raise InvalidCredentialsError()

    principal = repo.principal_for(user)
    mfa_satisfied = _check_mfa(user, principal, mfa_code, ip=ip)

    repo.record_login_success(user, now=now)
    issued = _issue_tokens(user, mfa_satisfied=mfa_satisfied)
    _persist_session(user, issued, ip=ip, user_agent=user_agent)

    record_audit(
        AuditAction.USER_LOGIN,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
    )

    dto = to_user_dto(user)
    return LoginResult(
        tokens=issued.pair,
        user=dto,
        roles=frozenset(str(r) for r in principal.roles),
    )


def _check_mfa(user: User, principal: Principal, mfa_code: str | None, *, ip: str | None) -> bool:
    """§30.2: TOTP is mandatory for PROVIDER_* and administrative roles."""
    if not mfa_mandatory(principal.roles):
        return bool(mfa_code) and _verify_totp_for(user, mfa_code)

    if not user.has_mfa:
        # A qualifying account that has never enrolled cannot reach the
        # console. Enrolment is a separate, authenticated-by-password flow.
        raise MfaRequiredError(
            "This account must enrol in two-factor authentication before signing in.",
            details=[{"code": "MFA_ENROLMENT_REQUIRED"}],
        )
    if not mfa_code:
        raise MfaRequiredError()
    if not _verify_totp_for(user, mfa_code):
        record_audit(
            AuditAction.USER_MFA_FAILED,
            entity_type="user",
            entity_id=str(user.public_id),
            actor_user_id=user.pk,
            ip=ip,
        )
        raise MfaRequiredError("That one-time code is not valid.")
    return True


def _verify_totp_for(user: User, code: str | None) -> bool:
    if not code or not user.mfa_secret:
        return False
    from ports.crypto import Ciphertext

    secret = get_crypto_port().decrypt(
        Ciphertext.from_bytes(bytes(user.mfa_secret)), aad=_mfa_aad(user)
    )
    return verify_totp(
        secret,
        code,
        at=_now(),
        drift_steps=int(get_setting("auth.totp_drift_steps")),
    )


def _mfa_aad(user: User) -> bytes:
    """Binds the ciphertext to this row and column — see `ports.crypto`."""
    return f"user:{user.public_id}:mfa_secret".encode()


def _register_failed_attempt(user: User, *, now: datetime, ip: str | None) -> None:
    decision = register_failure(
        failed_count=user.failed_login_count,
        window_started_at=user.failed_login_window_started_at,
        lockout_count=user.lockout_count,
        now=now,
        policy=_lockout_policy(),
    )

    if decision.is_locked:
        assert decision.locked_until is not None
        repo.apply_lockout(
            user, locked_until=decision.locked_until, window_started_at=decision.window_started_at
        )
        record_audit(
            AuditAction.USER_LOCKED,
            entity_type="user",
            entity_id=str(user.public_id),
            actor_user_id=user.pk,
            ip=ip,
            after={"locked_until": decision.locked_until.isoformat()},
        )
        if decision.notify_owner:
            # §30.2: "a notification to the account owner". The only signal a
            # user gets that someone is working through their password.
            _notify(
                user,
                subject="Your account has been temporarily locked",
                body=(
                    "<p>We locked your account after repeated failed sign-in attempts. "
                    "If this was not you, change your password once the lock lifts.</p>"
                ),
                template_id="account_locked",
            )
    else:
        repo.record_login_failure(
            user,
            count=decision.failed_count_after,
            window_started_at=decision.window_started_at,
        )

    record_audit(
        AuditAction.USER_LOGIN_FAILED,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
        reason="wrong_password",
    )


def _notify(user: User, *, subject: str, body: str, template_id: str) -> None:
    get_email_port().send(to=user.email, subject=subject, html_body=body, template_id=template_id)


# ---------------------------------------------------------------------------
# Refresh, with the reuse detection of §30.2
# ---------------------------------------------------------------------------


def refresh_tokens(raw_refresh: str, *, ip: str | None = None, user_agent: str = "") -> TokenPair:
    """Rotate a refresh token, or detect its reuse — §30.2.

    **Deliberately not wrapped in a single `atomic` block.** The reuse branch
    revokes the family and then raises, and under one transaction the raise
    would roll the revocation back — leaving a detected theft with every
    session still live. The revocation therefore commits in its own
    transaction *before* the error is raised, and the rotation has its own.
    """
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import RefreshToken

    now = _now()
    try:
        # The stubs type the constructor as taking a `Token`; it takes the
        # encoded string, which is the documented usage.
        presented = RefreshToken(raw_refresh)  # type: ignore[arg-type]
        jti = uuid.UUID(str(presented["jti"]))
    except (TokenError, KeyError, ValueError):
        # Unparseable or unsigned: there is no family to revoke.
        record_audit(
            AuditAction.USER_LOGIN_FAILED, entity_type="session", ip=ip, reason="bad_token"
        )
        raise AuthenticationError("That session token is not valid.") from None

    session = repo.load_session(jti)
    view = (
        None
        if session is None
        else TokenView(
            jti=session.jti,
            family_id=session.family_id,
            user_id=session.user_id,
            expires_at=session.expires_at,
            revoked_at=session.revoked_at,
            superseded_by=session.superseded_by,
        )
    )

    decision = evaluate_refresh(view, now=now)

    if decision.action is FamilyAction.REVOKE_FAMILY:
        # Its own transaction, committed before the raise below.
        with transaction.atomic():
            if decision.family_id is not None:
                repo.revoke_family(decision.family_id, now=now)
            if decision.reason is RefusalReason.SUPERSEDED:
                assert session is not None
                record_audit(
                    AuditAction.TOKEN_REUSE_DETECTED,
                    entity_type="session",
                    entity_id=str(jti),
                    actor_user_id=session.user_id,
                    ip=ip,
                    reason=str(decision.reason),
                )
        if decision.alert_owner and session is not None:
            _notify(
                session.user,
                subject="You have been signed out of all devices",
                body=(
                    "<p>We detected a sign-in token being reused, which can mean it was "
                    "copied. Every session has been ended. Please sign in again, and "
                    "change your password if you do not recognise this.</p>"
                ),
                template_id="session_reuse_detected",
            )
        raise AuthenticationError("That session token is not valid.")

    assert session is not None
    with transaction.atomic():
        # Carried forward: a rotation must not quietly downgrade a session
        # that satisfied MFA into one that did not.
        issued = _issue_tokens(
            session.user,
            family_id=session.family_id,
            mfa_satisfied=bool(presented.get("mfa", False)),
        )
        repo.supersede_session(session, successor=issued.jti)
        _persist_session(session.user, issued, ip=ip, user_agent=user_agent)

        record_audit(
            AuditAction.TOKEN_REFRESHED,
            entity_type="session",
            entity_id=str(issued.jti),
            actor_user_id=session.user_id,
            ip=ip,
        )
    return issued.pair


@transaction.atomic
def logout(*, principal: Principal, all_sessions: bool = False, ip: str | None = None) -> int:
    """End this principal's sessions. Returns how many were revoked."""
    user = User.objects.get(pk=principal.user_id)
    revoked = repo.revoke_all_families_for_user(user, now=_now())
    record_audit(
        AuditAction.USER_LOGOUT,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
        after={"sessions_revoked": revoked, "all_sessions": all_sessions},
    )
    return revoked


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def request_password_reset(*, email: str, ip: str | None = None) -> None:
    """§24.5: "The success message is identical whether or not the address
    exists, to avoid account enumeration."

    Returns `None` in both cases. The caller has nothing to branch on, which
    is the point — there is no shape of response that could leak the answer.
    """
    user = repo.find_user_by_email(email)
    if user is None:
        record_audit(
            AuditAction.USER_PASSWORD_RESET_REQUESTED,
            entity_type="user",
            ip=ip,
            reason="unknown_email",
        )
        return

    raw = repo.issue_one_time_token(
        user,
        TokenPurpose.PASSWORD_RESET,
        ttl=timedelta(minutes=int(get_setting("auth.password_reset_ttl_minutes"))),
        now=_now(),
    )
    _notify(
        user,
        subject="Reset your password",
        body=f"<p>Your password reset code is <code>{raw}</code>.</p>",
        template_id="password_reset",
    )
    record_audit(
        AuditAction.USER_PASSWORD_RESET_REQUESTED,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
    )


@transaction.atomic
def reset_password(*, token: str, new_password: str, ip: str | None = None) -> None:
    user = repo.consume_one_time_token(token, TokenPurpose.PASSWORD_RESET, now=_now())
    if user is None:
        raise ValidationError("That reset link is invalid or has expired.")

    _validate_new_password(new_password, email=user.email)
    repo.set_password(user, make_password(new_password))

    # Changing a password ends every session. Whoever prompted the reset may
    # already hold one, and leaving them signed in defeats the reset.
    revoked = repo.revoke_all_families_for_user(user, now=_now())

    record_audit(
        AuditAction.USER_PASSWORD_CHANGED,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
        after={"sessions_revoked": revoked},
    )


# ---------------------------------------------------------------------------
# MFA enrolment
# ---------------------------------------------------------------------------


def begin_mfa_enrolment(*, principal: Principal, issuer: str = "Pumba") -> dict[str, str]:
    """Generate a seed, store it encrypted, and return what the app scans.

    The seed is stored immediately but `mfa_enrolled_at` stays null until a
    code is verified — an enrolment that was started and abandoned must not
    lock the user out of their own account.
    """
    import secrets

    user = User.objects.get(pk=principal.user_id)
    secret = secrets.token_bytes(20)
    blob = get_crypto_port().encrypt(secret, aad=_mfa_aad(user))
    user.mfa_secret = blob.to_bytes()
    user.mfa_enrolled_at = None
    user.save(update_fields=["mfa_secret", "mfa_enrolled_at"])

    return {
        "secret": secret_to_base32(secret),
        "otpauth_uri": provisioning_uri(secret=secret, account=user.email, issuer=issuer),
    }


@transaction.atomic
def confirm_mfa_enrolment(*, principal: Principal, code: str, ip: str | None = None) -> None:
    user = User.objects.get(pk=principal.user_id)
    if not user.mfa_secret:
        raise ValidationError("Start enrolment before confirming it.")
    if not _verify_totp_for(user, code):
        record_audit(
            AuditAction.USER_MFA_FAILED,
            entity_type="user",
            entity_id=str(user.public_id),
            actor_user_id=user.pk,
            ip=ip,
        )
        raise ValidationError("That one-time code is not valid.")

    user.mfa_enrolled_at = _now()
    user.save(update_fields=["mfa_enrolled_at"])
    record_audit(
        AuditAction.USER_MFA_ENROLLED,
        entity_type="user",
        entity_id=str(user.public_id),
        actor_user_id=user.pk,
        ip=ip,
    )


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def register_device(
    *,
    principal: Principal,
    platform: str,
    push_token: str,
    device_name: str = "",
    app_version: str = "",
    ip: str | None = None,
) -> object:
    user = User.objects.get(pk=principal.user_id)
    device = repo.register_device(
        user,
        platform=platform,
        push_token=push_token,
        device_name=device_name,
        app_version=app_version,
    )
    record_audit(
        AuditAction.DEVICE_REGISTERED,
        entity_type="device",
        entity_id=str(device.public_id),
        actor_user_id=user.pk,
        ip=ip,
        after={"platform": platform},
    )
    return to_device_dto(device)


def remove_device(*, principal: Principal, public_id: uuid.UUID, ip: str | None = None) -> bool:
    """`False` when the device is not this principal's — the view renders 404.

    The lookup goes through the scoped selector, so a foreign device is never
    loaded and cannot be revoked.
    """
    from apps.identity.selectors import devices_visible_to

    device = devices_visible_to(principal, write=True).filter(public_id=public_id).first()
    if device is None:
        return False

    repo.revoke_device(device, now=_now())
    record_audit(
        AuditAction.DEVICE_REMOVED,
        entity_type="device",
        entity_id=str(public_id),
        actor_user_id=principal.user_id,
        ip=ip,
    )
    return True


def get_principal(*, user_id: int, mfa_satisfied: bool = False) -> Principal | None:
    """`get_principal()` from the §6.4 interface."""
    user = (
        User.objects.select_related("tourist_profile")
        .prefetch_related("user_roles__role")
        .filter(pk=user_id)
        .first()
    )
    return None if user is None else repo.principal_for(user, mfa_satisfied=mfa_satisfied)
