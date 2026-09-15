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
from ports.document import DocumentPort
from ports.exchange_rate import ExchangeRatePort
from ports.notification import EmailPort, PushPort, SmsPort
from ports.payment import PaymentGatewayPort
from ports.storage import StoragePort

logger = logging.getLogger(__name__)

__all__ = [
    "get_email_port",
    "get_sms_port",
    "get_push_port",
    "get_crypto_port",
    "get_breach_port",
    "get_storage_port",
    "get_exchange_rate_port",
    "get_document_port",
    "get_payment_port",
    "reset_ports",
    "AdapterNotConfiguredError",
]


class AdapterNotConfiguredError(RuntimeError):
    """A port with no default was asked for before it was configured.

    Not `ImproperlyConfigured`: this is raised at first use rather than at
    start-up, because the port is reached on one path and a deployment that
    never takes a payment should not fail to boot over it.
    """


#: Ports with no default adapter. Naming one is the only way to reach it, so a
#: deployment that forgets to configure a gateway raises at first use rather
#: than quietly taking imaginary money (ADR 0027, §21; Appendix D-1).
#:
#: `routing` keeps the stronger rule — no accessor at all — because §13.2
#: forbids persisting an unconfirmed geocode and `FakeRouting` answers with a
#: sha256-derived coordinate (Appendix D-2).
_NO_DEFAULT = {
    "payment": "ADR 0027, Appendix D-1. A gateway that reports success without "
    "one being configured is the most dangerous double in the system; §21 has "
    "real money behind it. `ci.py` names the fake explicitly; no production "
    "settings module does.",
}

#: The fake used when a port has no adapter configured.
_FAKES = {
    "email": "ports.fakes.FakeEmail",
    "sms": "ports.fakes.FakeSms",
    "push": "ports.fakes.FakePush",
    "crypto": "ports.fakes.FakeCrypto",
    "breach": "ports.fakes.FakeBreachedPasswords",
    "storage": "ports.fakes.FakeStorage",
    "exchange_rate": "ports.fakes.FakeExchangeRates",
    # ADR 0026. A renderer has no external side effect and no money behind it,
    # so a fake carries none of the risk that keeps routing and payment out.
    "document": "ports.fakes.FakeDocuments",
}


@cache
def _resolve(name: str) -> Any:
    configured = getattr(settings, "PORT_ADAPTERS", {}).get(name, "fake")
    if name in _NO_DEFAULT and configured == "fake":
        raise AdapterNotConfiguredError(
            f"No adapter is configured for the {name!r} port. {_NO_DEFAULT[name]}"
        )
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


def get_exchange_rate_port() -> ExchangeRatePort:
    """Indicative rates for display only. Never the §18.4 pricing path."""
    return _resolve("exchange_rate")  # type: ignore[no-any-return]


def get_document_port() -> DocumentPort:
    """Voucher rendering — ADR 0026."""
    return _resolve("document")  # type: ignore[no-any-return]


def get_payment_port() -> PaymentGatewayPort:
    """The PSP — §21, ADR 0027.

    Unlike every other accessor here, this one has no fallback: an unconfigured
    `payment` raises `AdapterNotConfiguredError` rather than resolving a fake.
    The fake is reachable only by being named in `PORT_ADAPTERS`, which the CI
    settings do and production settings must not.
    """
    return _resolve("payment")  # type: ignore[no-any-return]
