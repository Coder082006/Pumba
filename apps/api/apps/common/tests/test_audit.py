"""Tests for the audit write port — SRS §30.12, §41.13, §37.2."""

from __future__ import annotations

import logging

import pytest

from apps.common import audit, context
from apps.common.audit import AuditAction, AuditRecord, record_audit


@pytest.fixture(autouse=True)
def _isolate_sink():
    original = audit.get_sink()
    audit._sink = None
    context.reset_context()
    yield
    audit._sink = original
    context.reset_context()


class Recorder:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def __call__(self, record: AuditRecord) -> None:
        self.records.append(record)


class TestTheRecordReachesTheSink:
    def test_a_registered_sink_receives_the_record(self) -> None:
        recorder = Recorder()
        audit.register_sink(recorder)
        record_audit(AuditAction.USER_LOGIN, entity_type="user", entity_id="abc")
        assert len(recorder.records) == 1
        assert recorder.records[0].action is AuditAction.USER_LOGIN

    def test_the_record_is_returned_to_the_caller(self) -> None:
        """So a test can assert on it without reaching into the sink."""
        returned = record_audit(AuditAction.USER_LOGOUT, entity_type="user")
        assert returned.action is AuditAction.USER_LOGOUT

    def test_no_sink_is_not_an_error(self) -> None:
        """Before `administration` is ready, the port degrades to the log."""
        assert record_audit(AuditAction.USER_LOGIN, entity_type="user") is not None


class TestAuditingNeverFailsTheRequest:
    def test_a_raising_sink_does_not_propagate(self) -> None:
        """Otherwise an audit-store problem rolls back a completed login: the
        user is denied service *and* the event is lost."""

        def explode(record: AuditRecord) -> None:
            raise RuntimeError("audit table unreachable")

        audit.register_sink(explode)
        assert record_audit(AuditAction.USER_LOGIN, entity_type="user") is not None

    def test_a_sink_failure_is_logged_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        def explode(record: AuditRecord) -> None:
            raise RuntimeError("boom")

        audit.register_sink(explode)
        with caplog.at_level(logging.ERROR, logger="audit"):
            record_audit(AuditAction.USER_LOGIN, entity_type="user")
        assert "audit_sink_failed" in caplog.text

    def test_the_event_still_reaches_the_application_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An audit trail that exists in exactly one place is one outage from
        being gone."""

        def explode(record: AuditRecord) -> None:
            raise RuntimeError("boom")

        audit.register_sink(explode)
        with caplog.at_level(logging.INFO, logger="audit"):
            record_audit(AuditAction.USER_LOGIN, entity_type="user", entity_id="u1")
        assert any(r.__dict__.get("entity_id") == "u1" for r in caplog.records)


class TestContextDefaults:
    """§41.13 requires actor and request id on every entry, and both are
    already in contextvars — so a caller cannot forget them."""

    def test_request_id_defaults_from_the_request_context(self) -> None:
        context.set_request_id("req-123")
        assert record_audit(AuditAction.USER_LOGIN, entity_type="user").request_id == "req-123"

    def test_actor_defaults_from_the_request_context(self) -> None:
        context.set_actor_id(42)
        assert record_audit(AuditAction.USER_LOGIN, entity_type="user").actor_user_id == 42

    def test_an_explicit_actor_overrides_the_context(self) -> None:
        """A password reset is performed *on* a user by an unauthenticated
        request; the subject is not the context actor."""
        context.set_actor_id(42)
        record = record_audit(
            AuditAction.USER_PASSWORD_CHANGED, entity_type="user", actor_user_id=7
        )
        assert record.actor_user_id == 7

    def test_absent_context_leaves_the_fields_empty_rather_than_guessing(self) -> None:
        record = record_audit(AuditAction.USER_REGISTER, entity_type="user")
        assert record.actor_user_id is None
        assert record.request_id is None


class TestRecordShape:
    def test_before_and_after_default_to_empty_dicts(self) -> None:
        record = record_audit(AuditAction.USER_LOGIN, entity_type="user")
        assert record.before == {}
        assert record.after == {}

    def test_mutating_the_caller_s_dict_does_not_change_the_record(self) -> None:
        payload = {"status": "PENDING"}
        record = record_audit(AuditAction.USER_REGISTER, entity_type="user", after=payload)
        payload["status"] = "TAMPERED"
        assert record.after == {"status": "PENDING"}

    def test_the_record_is_immutable(self) -> None:
        record = record_audit(AuditAction.USER_LOGIN, entity_type="user")
        with pytest.raises(AttributeError):
            record.action = AuditAction.USER_LOGOUT  # type: ignore[misc]

    def test_occurred_at_is_timezone_aware(self) -> None:
        """SRS §7.2 forbids naive datetimes."""
        assert record_audit(AuditAction.USER_LOGIN, entity_type="user").occurred_at.tzinfo


class TestActionCatalogue:
    def test_every_action_is_dotted_and_lower_case(self) -> None:
        """The console filters on these; a stray format makes a category
        nobody ever queries."""
        for action in AuditAction:
            assert action.value == action.value.lower()
            assert "." in action.value

    def test_the_phase_2_events_are_all_present(self) -> None:
        """§37.2: "audit logging of all authentication events"."""
        required = {
            "user.register",
            "user.login",
            "user.login_failed",
            "user.logout",
            "user.locked",
            "user.password_changed",
            "user.mfa_enrolled",
            "token.reuse_detected",
        }
        assert required <= {a.value for a in AuditAction}
