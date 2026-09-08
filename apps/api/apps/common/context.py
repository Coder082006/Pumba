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

__all__ = [
    "get_request_id",
    "set_request_id",
    "get_actor_id",
    "set_actor_id",
    "get_display_currency",
    "set_display_currency",
    "reset_context",
]

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor_id: ContextVar[int | None] = ContextVar("actor_id", default=None)
_display_currency: ContextVar[str | None] = ContextVar("display_currency", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_actor_id() -> int | None:
    """The authenticated user id, bound for audit writes (SRS §30.12)."""
    return _actor_id.get()


def set_actor_id(value: int | None) -> None:
    _actor_id.set(value)


def get_display_currency() -> str | None:
    """What the tourist asked prices to be *shown* in — §9.1's `X-Currency`.

    A context variable rather than serializer context, for the same reason the
    request id is one: every money field in every module needs it, and
    threading `context=` through forty serializer constructions would mean the
    one that was missed rendered in the listing currency with nothing to say
    so. ADR 0005's argument, one layer down.

    **This is never the currency anything is charged in.** §18.4 gives the
    authoritative conversion its own mechanism — `fx_rate` frozen at
    `priced_at` — and `apps.common.display_money.IndicativeAmount` refuses to
    take part in arithmetic so the two cannot merge by accident (ADR 0024).
    """
    return _display_currency.get()


def set_display_currency(value: str | None) -> None:
    _display_currency.set(value)


def reset_context() -> None:
    _request_id.set(None)
    _actor_id.set(None)
    # Reset with the rest, and that matters more here than for the others: a
    # currency left behind would render the *next* request's prices in the
    # previous tourist's currency, on a worker that happened to reuse the task.
    _display_currency.set(None)
    # The rates fetched for that currency go with it. Imported here rather than
    # at module level because `presentment` reads this module — `common` is a
    # leaf and may not have a cycle inside it.
    from apps.common.presentment import reset_rate_cache

    reset_rate_cache()
