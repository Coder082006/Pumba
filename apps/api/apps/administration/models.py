"""administration module — SRS §6.4.

Owns:
        audit_log, system_setting, feature_flag, support_ticket

Interface:  record_audit(), get_setting()
Depends on: all (read via interfaces)
Layer:      L7

Owns the system_setting table and its audited write path. The *read*
port is apps.common.config.get_setting — see issue S1 in
docs/IMPLEMENTATION-PLAN.md for why it cannot live here.

Phase 2 builds `audit_log` only. §37.2 requires "audit logging of all
authentication events", and `identity` cannot import this module (it is L0
and depends on nothing), so the write port lives in `apps.common.audit` and
this is its sink — issue Q2, ADR 0005's sibling. `system_setting`,
`feature_flag` and `support_ticket` belong to the administration phase and
are deliberately absent.
"""

from __future__ import annotations

from django.db import models

from apps.common.models import TimestampedModel

__all__ = ["AuditLog"]


class AuditLog(TimestampedModel):
    """SRS §7.5.15, §41.13.

    Deliberately not a `BaseModel`: an audit row is never addressed by an API
    caller, so it needs no `public_id`, and deliberately not a
    `SoftDeleteModel`: §30.1 gives administrators "no delete rights" over the
    audit trail, so there is no deletion path to soften.

    §7.5.15 specifies monthly partitioning by `occurred_at` and 7-year
    retention for financial actions. Partitioning is a deployment concern
    handled with the production database in Phase 14, not by the ORM;
    recorded here so it is not mistaken for an omission.
    """

    occurred_at = models.DateTimeField(db_index=True)
    action = models.CharField(max_length=64, db_index=True)

    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64, null=True, blank=True)

    # Not a ForeignKey to identity.User on purpose. An audit row must outlive
    # the account it describes — §30.13 erasure closes an account and
    # anonymises its data while "audit log entries (integrity obligation)"
    # are retained — and a cascade or a protect would make that impossible.
    actor_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    actor_role = models.CharField(max_length=32, blank=True, default="")

    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=128, blank=True, default="")
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "audit_log"
        indexes = [
            # SRS §7.6: "INDEX(entity_type, entity_id); INDEX(actor_user_id,
            # occurred_at)" — investigation by subject, and by actor over time.
            models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx"),
            models.Index(fields=["actor_user_id", "-occurred_at"], name="audit_actor_time_idx"),
            models.Index(fields=["action", "-occurred_at"], name="audit_action_time_idx"),
        ]
        ordering = ["-occurred_at", "-id"]

    def __str__(self) -> str:
        return (
            f"{self.action} {self.entity_type}:{self.entity_id} @{self.occurred_at:%Y-%m-%d %H:%M}"
        )
