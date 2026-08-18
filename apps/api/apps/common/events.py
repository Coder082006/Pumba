"""Domain event bus.

SRS §8.9: "Events are the module decoupling mechanism. They are published
in-process **after commit** and consumed by Celery tasks."

SRS §8.3: "Domain events are dispatched after commit (`transaction.on_commit`)
so that no notification, ledger accrual or webhook can describe a state that
was rolled back."

That guarantee is the whole point of this module. `publish()` never dispatches
immediately — it queues on the current transaction and fires only if that
transaction commits. Outside a transaction (management commands, tests) the
handler runs immediately, which `transaction.on_commit` already does for us.

This is the mechanism only. The event catalogue of SRS §8.9 — `TripConfirmed`,
`BookingCancelled`, `PaymentCaptured` and the rest — is declared by the
publishing modules as they are built.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, TypeVar, overload
from uuid import uuid4

from django.db import transaction

from apps.common.context import get_request_id

logger = logging.getLogger(__name__)

__all__ = ["DomainEvent", "subscribe", "publish", "clear_subscribers", "get_subscribers"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Base class for every domain event.

    Subclasses carry only primitives and identifiers — never ORM instances.
    A handler runs in a different transaction (often a different process), so
    a passed-in model object would be stale by the time it is read.
    """

    name: ClassVar[str] = "domain.event"

    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    request_id: str | None = field(default_factory=get_request_id)

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        payload = asdict(self)
        payload["name"] = self.name
        payload["occurred_at"] = self.occurred_at.isoformat()
        return payload


E = TypeVar("E", bound=DomainEvent)

Handler = Callable[[Any], None]

_subscribers: dict[type[DomainEvent], list[Handler]] = defaultdict(list)


@overload
def subscribe(event_type: type[E], handler: Callable[[E], None]) -> Callable[[E], None]: ...


@overload
def subscribe(event_type: type[E]) -> Callable[[Callable[[E], None]], Callable[[E], None]]: ...


def subscribe(event_type: type[E], handler: Callable[[E], None] | None = None) -> Any:
    """Register a handler for `event_type`.

    Callable directly::

        subscribe(BookingConfirmed, on_booking_confirmed)

    or as a decorator, which is how consumers usually read best::

        @subscribe(BookingConfirmed)
        def on_booking_confirmed(event: BookingConfirmed) -> None:
            ...

    Handlers should enqueue a Celery task rather than do work inline — the
    publishing request should not pay for a consumer's latency (SRS §8.9).
    """
    if handler is None:

        def register(func: Callable[[E], None]) -> Callable[[E], None]:
            _subscribers[event_type].append(func)  # type: ignore[arg-type]
            return func

        return register

    _subscribers[event_type].append(handler)  # type: ignore[arg-type]
    return handler


def get_subscribers(event_type: type[DomainEvent]) -> list[Handler]:
    return list(_subscribers[event_type])


def clear_subscribers() -> None:
    """Test helper. Never call from application code."""
    _subscribers.clear()


def publish(event: DomainEvent) -> None:
    """Queue `event` for dispatch after the current transaction commits.

    If the transaction rolls back the event is never dispatched, which is the
    invariant that keeps notifications and ledger entries honest.
    """
    handlers = _subscribers.get(type(event), [])
    if not handlers:
        logger.debug("event_published_no_subscribers", extra={"event_name": event.name})
        return

    def _dispatch() -> None:
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # One failing consumer must not prevent the others from
                # running, and must never roll back the already-committed
                # publisher. Celery retry policy (SRS §8.8) owns recovery.
                logger.exception(
                    "event_handler_failed",
                    extra={
                        "event_name": event.name,
                        "event_id": event.event_id,
                        "handler": getattr(handler, "__qualname__", repr(handler)),
                    },
                )

    transaction.on_commit(_dispatch)
