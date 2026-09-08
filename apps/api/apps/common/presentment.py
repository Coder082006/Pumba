"""Prices a tourist can read — SRS §9.1, §24.1, §24.11, ADR 0024.

Every price this platform stores is in the destination's currency. §4.2 is
emphatic that this is right: a currency is "resolved from destination.country",
never hard-coded. It is also unreadable to the tourists the platform is built
for, who arrive from Germany, Britain and America and cannot tell whether
`TZS 94,500` is cheap.

So a price goes over the wire twice: once as the amount that will be charged,
and once converted into whatever the tourist asked to see. §9.1 gives them
`X-Currency` and §24.1 gives them a chooser; this is what turns the header into
a number on a page.

**The converted figure cannot become money.** `convert_for_display` returns an
`IndicativeAmount`, whose arithmetic operators raise by construction, and this
module never unwraps one into a `Decimal` a caller could add up. §18.4 gives
the *charged* conversion an entirely separate mechanism — `fx_rate` rows frozen
at `priced_at`, owned by `finance` — and ADR 0024 records the line between them.
The type is the enforcement; this module is only careful not to defeat it.

**An unavailable rate is silence, not a guess.** `ExchangeRatePort` returns
`None` for a pair it cannot price, and the page then shows the listing's own
currency with no conversion beside it. No stale rate, no hard-coded one, no
last-known value — the same discipline ADR 0019 applied to the distance chip: a
figure a tourist could mistake for fact is never fabricated.
"""

from __future__ import annotations

from decimal import Decimal

from apps.common.config import get_setting
from apps.common.context import get_display_currency
from apps.common.display_money import IndicativeAmount, convert_for_display
from apps.common.money import Money
from apps.common.ports_registry import get_exchange_rate_port

__all__ = ["enabled_currencies", "is_enabled", "shown_in", "display_of"]


def enabled_currencies() -> tuple[str, ...]:
    """§24.1's chooser, from `currency.enabled`.

    A `system_setting` row rather than a constant, so adding CHF is an
    administrator's edit and not a deployment — which is the whole reason
    hard rule 5 exists.
    """
    raw = get_setting("currency.enabled")
    values = raw if isinstance(raw, list | tuple) else [raw]
    return tuple(str(code).upper() for code in values)


def is_enabled(code: str | None) -> bool:
    return bool(code) and str(code).upper() in enabled_currencies()


def shown_in(source_currency: str) -> str | None:
    """The currency this request wants prices shown in, or `None`.

    `None` means "show the listing's own", which is the answer in three
    different situations that all look the same on screen and should: the
    tourist asked for nothing, they asked for the currency it is already in, or
    they asked for one the platform is not configured to show.

    The last of those is deliberately silent. §9.1's header is a display
    preference, and failing a public catalogue read because somebody's browser
    remembered a currency an administrator has since retired would turn a
    cosmetic mistake into an outage — which is the reasoning `LocaleMiddleware`
    already applies to a malformed code.
    """
    wanted = get_display_currency()
    if not is_enabled(wanted):
        return None
    assert wanted is not None
    wanted = wanted.upper()
    return None if wanted == source_currency.upper() else wanted


def display_of(amount: Decimal | None, currency: str | None) -> IndicativeAmount | None:
    """The converted figure to render beside a price, or `None`.

    `None` at every step that cannot honestly produce a number: no price, no
    currency, nothing to convert to, or no rate for the pair. A caller renders
    the field only when this returns something, so the wire carries no key
    rather than a null — the difference matters, because a null invites a
    client to print an empty string where a price should be.
    """
    if amount is None or not currency:
        return None
    target = shown_in(currency)
    if target is None:
        return None
    rate = get_exchange_rate_port().indicative_rate(base=currency.upper(), quote=target)
    if rate is None:
        return None
    return convert_for_display(Money(amount, currency), rate=rate)
