"""Structured JSON logging — SRS §8.11, §32.6.

Every line carries `request_id` where one is in scope. PII and secrets are
redacted by a filter so that a careless `logger.info(payload)` cannot leak a
password or a card reference into the log store.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from apps.common.context import get_actor_id, get_request_id

__all__ = ["RequestIdFilter", "JsonFormatter", "REDACTED_KEYS"]

# Substring match, case-insensitive.
REDACTED_KEYS = frozenset(
    {
        "password",
        "token",
        "secret",
        "authorization",
        "card",
        "cvv",
        "pan",
        "mfa",
        "otp",
        "api_key",
        "client_secret",
    }
)

_REDACTION = "[REDACTED]"

_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "asctime",
    "message",
}


def _redact(value: Any, key: str = "") -> Any:
    if any(marker in key.lower() for marker in REDACTED_KEYS):
        return _REDACTION
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v, key) for v in value]
    return value


class RequestIdFilter(logging.Filter):
    """Attach the current request id and actor to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.actor_id = get_actor_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "actor_id": getattr(record, "actor_id", None),
        }

        # Anything passed via `extra=`.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key not in payload:
                payload[key] = _redact(value, key)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
