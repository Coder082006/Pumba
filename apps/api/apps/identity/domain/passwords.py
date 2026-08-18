"""Password policy — SRS §30.2, §9.4.1.

    "Minimum 12 characters, checked against a breached-credential list; no
     forced periodic rotation."

    §9.4.1: "password minimum 12 characters, checked against a breached-
     password list, not equal to email local-part"

Violations are returned rather than raised, so the serializer can map each one
onto a `details[].field` path — SRS §24.3 requires the registration screen to
place errors on fields by that path, and an exception carrying one message
cannot express two simultaneous failures.

`is_breached` arrives as a bool computed by the application layer. The port
call stays outside the domain, which is what lets the rule be tested without a
network and what stops a third party's availability becoming a property of
this function.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "PasswordViolationCode",
    "PasswordViolation",
    "validate_password",
    "email_local_part",
]


class PasswordViolationCode(StrEnum):
    TOO_SHORT = "TOO_SHORT"
    BREACHED = "BREACHED"
    EQUALS_EMAIL_LOCAL_PART = "EQUALS_EMAIL_LOCAL_PART"


@dataclass(frozen=True, slots=True)
class PasswordViolation:
    code: PasswordViolationCode
    message: str


def email_local_part(email: str) -> str:
    """The part before the first `@`, lowercased.

    Splits on the *first* `@` rather than the last: an address may legally
    quote an `@` inside the local part, and taking the last would compare
    against something that is not the local part at all.
    """
    return email.split("@", 1)[0].strip().casefold()


def validate_password(
    password: str,
    *,
    email: str,
    min_length: int,
    is_breached: bool,
) -> tuple[PasswordViolation, ...]:
    """Every way this password fails the policy, not just the first.

    Length is checked in code points, not bytes: a 12-character passphrase in
    a non-Latin script is 12 characters, and counting its UTF-8 bytes would
    quietly hold some users to a weaker rule than others.
    """
    violations: list[PasswordViolation] = []

    if len(password) < min_length:
        violations.append(
            PasswordViolation(
                PasswordViolationCode.TOO_SHORT,
                f"Password must be at least {min_length} characters.",
            )
        )

    if is_breached:
        violations.append(
            PasswordViolation(
                PasswordViolationCode.BREACHED,
                "This password has appeared in a known data breach. Choose another.",
            )
        )

    local = email_local_part(email)
    if local and password.casefold() == local:
        violations.append(
            PasswordViolation(
                PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART,
                "Password must not be your email address.",
            )
        )

    return tuple(violations)
