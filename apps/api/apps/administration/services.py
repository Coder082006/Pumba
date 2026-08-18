"""administration module — SRS §6.4.

Owns:
        audit_log, system_setting, feature_flag, support_ticket

Interface:  record_audit(), get_setting()
Depends on: all (read via interfaces)
Layer:      L7

Application layer (SRS §8.2 layer 2).

The ONLY module boundary. Other modules call this and nothing else
(SRS §6.5 rule 1). Orchestrates a use case in one transaction and
emits domain events.

Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

Phase 2 implements the audit sink only.
"""

from __future__ import annotations

from apps.administration.models import AuditLog
from apps.common.audit import AuditRecord

__all__ = ["write_audit_record"]


def write_audit_record(record: AuditRecord) -> None:
    """The sink registered on `apps.common.audit` at startup.

    Takes the port's value object rather than keyword arguments so that a new
    field on `AuditRecord` cannot be silently dropped in transit — it either
    lands in a column here or fails the type check.
    """
    AuditLog.objects.create(
        occurred_at=record.occurred_at,
        action=str(record.action),
        entity_type=record.entity_type,
        entity_id=record.entity_id,
        actor_user_id=record.actor_user_id,
        actor_role=record.actor_role or "",
        before=record.before,
        after=record.after,
        ip=record.ip,
        request_id=record.request_id or "",
        reason=record.reason,
    )
