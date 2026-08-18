"""Which adapter implements each port, resolved once per process.

Brief constraint: *"No vendor SDK may be imported outside `adapters/`. No
provider is selected yet."* Every port therefore resolves to its deterministic
fake unless a real adapter is configured, and the configuration is a dotted
path in Django settings rather than a branch in business logic — so selecting
a provider later is a settings change and an adapter, never an edit to a
service.

`PORT_ADAPTERS` maps a port name to a dotted path or to `"fake"`:

    PORT_ADAPTERS = {
        "email": "apps.notify.adapters.ses.SesEmailAdapter",
        "crypto": "fake",
    }

An unresolvable path raises at first use rather than falling back to the fake.
Silently sending live notifications through a fake — or worse, storing a
passport reference under `FakeCrypto` — is a far worse failure than a loud
one at startup.
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string

from ports.breach import BreachedPasswordPort
from ports.crypto import CryptoPort
from ports.notification import EmailPort, PushPort, SmsPort
from ports.storage import StoragePort

logger = logging.getLogger(__name__)

__all__ = [
    "get_email_port",
    "get_sms_port",
    "get_push_port",
    "get_crypto_port",
    "get_breach_port",
    "get_storage_port",
    "reset_ports",
]

#: The fake used when a port has no adapter configured.
_FAKES = {
    "email": "ports.fakes.FakeEmail",
    "sms": "ports.fakes.FakeSms",
    "push": "ports.fakes.FakePush",
    "crypto": "ports.fakes.FakeCrypto",
    "breach": "ports.fakes.FakeBreachedPasswords",
    "storage": "ports.fakes.FakeStorage",
}


@cache
def _resolve(name: str) -> Any:
    configured = getattr(settings, "PORT_ADAPTERS", {}).get(name, "fake")
    path = _FAKES[name] if configured == "fake" else configured
    if configured == "fake":
        logger.warning(
            "port_using_fake_adapter",
            extra={"port": name, "adapter": path},
        )
    return import_string(path)()


def reset_ports() -> None:
    """Drop the cached adapters. For tests that reconfigure them."""
    _resolve.cache_clear()


def get_email_port() -> EmailPort:
    return _resolve("email")  # type: ignore[no-any-return]


def get_sms_port() -> SmsPort:
    return _resolve("sms")  # type: ignore[no-any-return]


def get_push_port() -> PushPort:
    return _resolve("push")  # type: ignore[no-any-return]


def get_crypto_port() -> CryptoPort:
    return _resolve("crypto")  # type: ignore[no-any-return]


def get_breach_port() -> BreachedPasswordPort:
    return _resolve("breach")  # type: ignore[no-any-return]


def get_storage_port() -> StoragePort:
    return _resolve("storage")  # type: ignore[no-any-return]
