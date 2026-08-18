"""PushPort, EmailPort, SmsPort — SRS §19, §34.8.

Three separate protocols rather than one, because they fail and are replaced
independently. SRS §34.8: "All three behind ports, because SMS providers in
particular are frequently changed for cost and deliverability reasons."

Every method returns a `DeliveryResult` rather than raising on a rejected
recipient: SRS §19.4 requires per-recipient delivery auditing, and an
exception would lose the outcome of the other recipients in a batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = [
    "DeliveryStatus",
    "DeliveryResult",
    "PushPort",
    "EmailPort",
    "SmsPort",
]


class DeliveryStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: DeliveryStatus
    provider_message_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is DeliveryStatus.ACCEPTED


@runtime_checkable
class PushPort(Protocol):
    def send(
        self,
        *,
        device_token: str,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> DeliveryResult: ...


@runtime_checkable
class EmailPort(Protocol):
    def send(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        template_id: str | None = None,
        context: dict[str, str] | None = None,
    ) -> DeliveryResult: ...


@runtime_checkable
class SmsPort(Protocol):
    def send(self, *, to_e164: str, body: str) -> DeliveryResult: ...
