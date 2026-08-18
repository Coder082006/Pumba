"""administration module — SRS §6.4.

Owns:       audit_log, system_setting, feature_flag, support_ticket
Interface:  record_audit(), get_setting()
Depends on: all (read via interfaces)
Layer:      L7

Owns the system_setting table and its audited write path. The *read*
port is apps.common.config.get_setting — see issue S1 in
docs/IMPLEMENTATION-PLAN.md for why it cannot live here.
"""
