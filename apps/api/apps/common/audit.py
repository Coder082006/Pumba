"""Audit write port — SRS §30.12, §41.13.

    §41.13: "Every administrative action writes an audit entry with before and
     after state, actor, role, IP and request id."

    §37.2: Phase 2 delivers "audit logging of all authentication events".

**Why this lives in `common` and not in `administration`.**

SRS §6.4 assigns the `audit_log` table to `administration`, which sits at the
bottom of the dependency order and may read everything. But `identity` is at
the top and depends on nothing, so `identity` cannot import `administration`
to write an audit entry — and every other module has the same problem.

This is the same shape as issue S1, where `system_setting` is owned by
`administration` but read by all fourteen modules, and it is resolved the same
way: split the *write port* from the *table*. This module is the port and
lives in `common`, which every module may import. `administration` owns the
table, the query path, the retention policy and the console, and registers a
database-backed sink here at startup.

**Auditing must not be able to fail a request it is recording.** A sink that
raises would let an audit-store problem roll back a completed login, which is
worse than a missing audit line — the user is denied service *and* the event
is lost. So the sink's exceptions are caught and logged at ERROR. The record
is never silently dropped: it always reaches the application log even when the
table is unreachable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from apps.common.context import get_actor_id, get_request_id

logger = logging.getLogger("audit")

__all__ = [
    "AuditAction",
    "AuditRecord",
    "record_audit",
    "register_sink",
    "get_sink",
]


class AuditAction(StrEnum):
    """Authentication and authorisation events — the Phase 2 set.

    Other modules add their own members as their phases land. Kept as one
    enum rather than free strings so the console can filter on a closed set
    and a typo cannot create a category nobody ever queries.
    """

    USER_REGISTER = "user.register"
    USER_EMAIL_VERIFIED = "user.email_verified"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_LOCKED = "user.locked"
    USER_PASSWORD_RESET_REQUESTED = "user.password_reset_requested"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_MFA_ENROLLED = "user.mfa_enrolled"
    USER_MFA_VERIFIED = "user.mfa_verified"
    USER_MFA_FAILED = "user.mfa_failed"
    TOKEN_REFRESHED = "token.refreshed"
    TOKEN_REUSE_DETECTED = "token.reuse_detected"
    SESSION_FAMILY_REVOKED = "session.family_revoked"
    DEVICE_REGISTERED = "device.registered"
    DEVICE_REMOVED = "device.removed"
    AUTHORISATION_DENIED = "authorisation.denied"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    action: AuditAction
    entity_type: str
    entity_id: str | None = None
    actor_user_id: int | None = None
    actor_role: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    ip: str | None = None
    request_id: str | None = None
    reason: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


#: Installed by `administration` at startup. Signature: (AuditRecord) -> None.
_sink: Callable[[AuditRecord], None] | None = None


def register_sink(sink: Callable[[AuditRecord], None]) -> None:
    """Install the database-backed writer. Called by `administration`."""
    global _sink
    _sink = sink


def get_sink() -> Callable[[AuditRecord], None] | None:
    return _sink


def record_audit(
    action: AuditAction,
    *,
    entity_type: str,
    entity_id: str | None = None,
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    reason: str = "",
) -> AuditRecord:
    """Record one auditable event.

    `request_id` and `actor_user_id` default from the request context, so a
    caller cannot forget them — §41.13 requires both on every entry and the
    values are already in `contextvars` for the current request.

    Returns the record so a caller can assert on it in tests without reaching
    into the sink.
    """
    record = AuditRecord(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=actor_user_id if actor_user_id is not None else get_actor_id(),
        actor_role=actor_role,
        before=dict(before or {}),
        after=dict(after or {}),
        ip=ip,
        request_id=get_request_id(),
        reason=reason,
    )

    # Always to the application log, even when the table is reachable: the two
    # have different retention and different blast radius, and an audit trail
    # that exists in exactly one place is one outage from being gone.
    logger.info(
        record.action,
        extra={
            "audit_action": str(record.action),
            "entity_type": record.entity_type,
            "entity_id": record.entity_id,
            "actor_user_id": record.actor_user_id,
            "request_id": record.request_id,
        },
    )

    if _sink is not None:
        try:
            _sink(record)
        except Exception:
            # Never let auditing fail the request it is recording: the user
            # would be denied service *and* the event lost.
            logger.exception("audit_sink_failed", extra={"audit_action": str(record.action)})

    return record
