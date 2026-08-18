"""Per-request context.

`contextvars`, not thread-locals: the API runs under ASGI, where a single
thread interleaves many requests and a thread-local would leak one request's
identity into another's log lines.

SRS §32.6: the request id flows into the log context, into every downstream
call, into Celery task headers, and into the error response, so support can
take an id from a screenshot and reconstruct the whole causal chain.
"""

from __future__ import annotations

from contextvars import ContextVar

__all__ = ["get_request_id", "set_request_id", "get_actor_id", "set_actor_id", "reset_context"]

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor_id: ContextVar[int | None] = ContextVar("actor_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_actor_id() -> int | None:
    """The authenticated user id, bound for audit writes (SRS §30.12)."""
    return _actor_id.get()


def set_actor_id(value: int | None) -> None:
    _actor_id.set(value)


def reset_context() -> None:
    _request_id.set(None)
    _actor_id.set(None)
